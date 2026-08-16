"""Build the static trainer into dist/, ready for GitHub Pages.

    python -m scripts.export_static [--out dist] [--no-precompute]

Emits the frontend with MODE='static', the vendored assets, and four JSON
files under data/. The 21k-row evals table stays home: insights is precomputed
here, and the only eval the trainer reads at runtime (the puzzle's own) is
baked into each puzzle record.
"""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import chess

from chess_trainer import (PROJECT_ROOT, accept, db, insights, judgments,
                           load_config)

DIST = PROJECT_ROOT / "dist"
WEB = PROJECT_ROOT / "web"

# Copied verbatim from web/. Anything not listed is not shipped.
COPY_TREES = ["css", "js", "vendor", "icons"]
COPY_FILES = ["index.html", "train.html", "insights.html", "manifest.webmanifest"]


def _hash(fen: str, best_uci: str, judgment: str) -> str:
    """Content hash: changes exactly when a puzzle's answer changes.

    The client resets a card's schedule when this moves, so it must cover what
    makes the puzzle a different question and nothing else. Explanation edits
    deliberately do not count.
    """
    return hashlib.sha1(f"{fen}|{best_uci}|{judgment}".encode()).hexdigest()[:8]


def build_puzzles(conn, config) -> list[dict]:
    accept_sets = accept.load_all(conn, config)
    evals = {
        (r["game_id"], r["ply"]): r
        for r in conn.execute("SELECT game_id, ply, score_cp, score_mate FROM evals")
    }
    rows = conn.execute(
        """SELECT m.*, g.opening FROM mistakes m
           JOIN games g ON g.id = m.game_id
           WHERE m.discarded_at IS NULL
           ORDER BY m.game_id, m.ply"""
    ).fetchall()

    out, missing_accept = [], 0
    for r in rows:
        key = (r["game_id"], r["ply"])
        moves, complete = accept_sets.get(key, ([], 0))
        if key not in accept_sets:
            missing_accept += 1

        p = {
            "p": f"{r['game_id']}:{r['ply']}",
            "f": r["fen"],
            "c": r["color"],
            "n": r["ply"] // 2 + 1,
            "ps": r["played_san"],
            "pu": r["played_uci"],
            "bs": r["best_san"],
            "bu": r["best_uci"],
            "wb": r["win_before"],
            "wl": r["win_loss"],
            "j": r["judgment"],
            "ph": r["phase"],
            "mv": moves,
            "mvc": int(complete),
            "h": _hash(r["fen"], r["best_uci"], r["judgment"]),
        }
        if r["pv_san"]:
            p["pv"] = r["pv_san"]
        if r["explanation"]:
            p["e"] = r["explanation"]
        if r["explanation_played"]:
            p["ep"] = r["explanation_played"]
        if r["motif"]:
            p["mo"] = r["motif"]
        if r["phase"] == "endgame":
            p["eg"] = insights.classify_endgame(r["fen"])
        # The trainer's only use of the evals table: the eval shown next to the
        # best move after a reveal. White-relative, like every eval on screen.
        ev = evals.get(key)
        if ev:
            p["ev"] = judgments.fmt_eval(ev["score_cp"], ev["score_mate"], chess.WHITE)
        out.append(p)

    if missing_accept:
        print(f"  ! {missing_accept} puzzles have no accept set - alternatives to the "
              f"engine move will fall back to the phone engine")
    return out


def build_games(conn) -> dict:
    return {
        r["id"]: {"o": r["opponent"], "s": r["speed"], "t": r["played_at"],
                  "ur": r["user_rating"], "or": r["opponent_rating"],
                  "op": r["opening"], "r": r["result"]}
        for r in conn.execute(
            """SELECT DISTINCT g.id, g.opponent, g.speed, g.played_at, g.user_rating,
                      g.opponent_rating, g.opening, g.result
               FROM games g JOIN mistakes m ON m.game_id = g.id
               WHERE m.discarded_at IS NULL""")
    }


def check_report_links(stats: dict) -> list[str]:
    """Catch coach-report verdicts whose training link contradicts the claim.

    "opening" means two different things here — the game phase and the opening
    family — and a verdict headlined "Opening play is your strongest phase"
    once linked to `opening=Caro-Kann Defense`, i.e. whole games in one
    repertoire, 92% of which were not the opening phase.
    """
    warnings = []
    for verdict in (stats.get("report") or {}).get("verdicts", []) or []:
        training = verdict.get("training")
        if not training:
            continue
        url, headline = training.get("url", ""), verdict.get("headline", "")
        if "phase" in headline.lower() and "phases=" not in url and "endgame=" not in url:
            warnings.append(
                f"verdict {headline!r} claims a game phase but its link "
                f"({url}) does not filter by one")
    return warnings


def write_json(path: Path, payload) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def copy_frontend(out: Path, build_id: str) -> list[str]:
    """Copy web/ into dist/ and return the precache list for the service worker."""
    assets = []
    for name in COPY_TREES:
        src = WEB / name
        if not src.exists():
            continue
        shutil.copytree(src, out / name, dirs_exist_ok=True)
        for f in sorted(src.rglob("*")):
            if f.is_file():
                assets.append("./" + f.relative_to(WEB).as_posix())
    for name in COPY_FILES:
        if (WEB / name).exists():
            shutil.copy2(WEB / name, out / name)
            assets.append("./" + name)

    # The build's own mode switch. Overwrites the copy of web/js/config.js.
    (out / "js" / "config.js").write_text(
        "/* Generated by scripts/export_static.py — do not edit in dist/. */\n"
        "export const MODE = 'static';\n"
        f"export const BUILD_ID = '{build_id}';\n",
        encoding="utf-8")
    return assets


def write_service_worker(out: Path, build_id: str, assets: list[str]) -> None:
    template = (WEB / "sw.js").read_text(encoding="utf-8")
    precache = [a for a in assets
                if not a.startswith("./vendor/stockfish/")
                and not a.endswith(".map")]
    precache += ["./", "./data/manifest.json", "./data/puzzles.json",
                 "./data/games.json", "./data/insights.json"]
    body = (template
            .replace("__BUILD_ID__", build_id)
            .replace("'__PRECACHE__'", json.dumps(sorted(set(precache)), indent=2)))
    (out / "sw.js").write_text(body, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DIST), help="output directory (default: dist)")
    ap.add_argument("--no-precompute", action="store_true",
                    help="skip the accept-set pass (puzzles without one fall back "
                         "to the phone engine)")
    ap.add_argument("--no-report", action="store_true",
                    help="omit the Claude-authored coach report from insights.json")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    config = load_config()
    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")
    build_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    if not args.no_precompute:
        print("Precomputing accept sets…")
        result = accept.ensure(conn, config)
        print(f"  {result['computed']} computed, {result['cached']} cached")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print(f"Building {out} (build {build_id})")
    assets = copy_frontend(out, build_id)

    puzzles = build_puzzles(conn, config)
    games = build_games(conn)
    stats = insights.compute_insights(conn, force=True)
    if args.no_report:
        stats = {**stats, "report": None, "report_stale": False}

    for warning in check_report_links(stats):
        print(f"  ! {warning}")

    games_row = conn.execute(
        "SELECT COUNT(*) AS total, COUNT(analyzed_at) AS analyzed FROM games").fetchone()
    by_judgment: dict[str, int] = {}
    for p in puzzles:
        by_judgment[p["j"]] = by_judgment.get(p["j"], 0) + 1

    manifest = {
        "build_id": build_id,
        "generated_at": db.now_s(),
        "username": config["lichess"]["username"],
        "tolerance_winpct": config["trainer"]["tolerance_winpct"],
        "accept_nodes": config["trainer"]["check_nodes"],
        "accept_multipv": accept.MULTIPV,
        "counts": {
            "games": games_row["total"],
            "games_analyzed": games_row["analyzed"],
            "puzzles": len(puzzles),
            "by_judgment": by_judgment,
        },
        "date_range": stats["sample"]["date_range"],
    }

    sizes = {
        "puzzles.json": write_json(out / "data" / "puzzles.json", puzzles),
        "games.json": write_json(out / "data" / "games.json", games),
        "insights.json": write_json(out / "data" / "insights.json", stats),
        "manifest.json": write_json(out / "data" / "manifest.json", manifest),
    }

    write_service_worker(out, build_id, assets)
    # Skip Jekyll entirely: faster, and it would otherwise ignore _-prefixed paths.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n  {len(puzzles)} puzzles, {len(games)} games")
    for name, n in sizes.items():
        print(f"  data/{name:16s} {n / 1024:8.1f} KB")
    print(f"  {'total dist/':21s} {total / 1024 / 1024:8.2f} MB")


if __name__ == "__main__":
    main()
