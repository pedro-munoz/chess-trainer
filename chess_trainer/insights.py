"""Strength/weakness statistics computed from stored evals, games and mistakes.

Read-only. Rates are per 100 own-moves (denominators come from the full eval
curves), so both weaknesses and strengths are claimable. Results are cached
in-process and invalidated by a cheap SQL probe, so an out-of-process
`analyze` run is picked up on the next request.

Human-facing verdicts live in data/insights_report.json, authored by Claude
Code from these numbers (see CLAUDE.md); this module only serves honest stats
and statistical signals.
"""

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from . import DATA_DIR, judgments

REPORT_PATH = DATA_DIR / "insights_report.json"

BANDS = ((10, "1-10"), (20, "11-20"), (30, "21-30"), (40, "31-40"), (10 ** 9, "41+"))
# Evaluations are clamped to +-10 pawns before measuring loss: past that the game
# is already decided and further swings say nothing about move quality.
CP_CAP = 1000
GAP_THRESHOLD = 50  # rating points that make an opponent "stronger"/"weaker"
DRILL_JUDGMENTS = ("blunder", "mistake")

# Minimum own-moves before a bucket can produce a signal (it still shows in tables).
MIN_MOVES = {"color": 800, "phase": 400, "phase_color": 250,
             "opening": 150, "band": 300, "gap": 400, "endgame": 250}
MIN_GAMES_OPENING = 8


def _phase_from_fen(fen: str, ply: int,
                    division_middle: int | None, division_end: int | None) -> str:
    """Same result as judgments.phase_of, without building a chess.Board.

    Must mirror that function exactly (majors+minors = pieces other than
    kings/pawns, counted straight off the FEN piece field).
    """
    if division_middle is not None or division_end is not None:
        if division_end is not None and ply >= division_end:
            return "endgame"
        if division_middle is not None and ply >= division_middle:
            return "middlegame"
        return "opening"
    piece_field = fen.split(" ", 1)[0]
    mm = sum(piece_field.count(c) for c in "NBRQnbrq")
    if mm <= 6:
        return "endgame"
    if ply >= 20 or mm <= 10:
        return "middlegame"
    return "opening"


def _family(opening: str | None) -> str:
    return (opening or "Unknown").split(":")[0].strip()


def _pawn_loss(cp_before: int, cp_after: int, is_white: bool) -> float:
    """Evaluation given away by one move, in pawns (>= 0), mover's perspective."""
    before = cp_before if is_white else -cp_before
    after = cp_after if is_white else -cp_after
    before = max(-CP_CAP, min(CP_CAP, before))
    after = max(-CP_CAP, min(CP_CAP, after))
    return max(0.0, (before - after) / 100.0)


def classify_endgame(fen: str) -> str:
    """Endgame type from the pieces still on the board (kings/pawns aside)."""
    field = fen.split(" ", 1)[0]
    pieces = {c.upper() for c in field if c in "NBRQnbrq"}
    if not pieces:
        return "pawn"
    if "Q" in pieces:
        return "queen" if pieces == {"Q"} else "queen + pieces"
    if "R" in pieces:
        return "rook" if pieces == {"R"} else "rook + minor"
    if pieces == {"B"}:
        return "bishop"
    if pieces == {"N"}:
        return "knight"
    return "knight + bishop"


def _band(move_no: int) -> str:
    for limit, name in BANDS:
        if move_no <= limit:
            return name
    return BANDS[-1][1]


def _gap_band(user_rating: int | None, opponent_rating: int | None) -> str | None:
    if not user_rating or not opponent_rating:
        return None
    diff = opponent_rating - user_rating
    if diff >= GAP_THRESHOLD:
        return "stronger"
    if diff <= -GAP_THRESHOLD:
        return "weaker"
    return "similar"


def _month(played_at_ms: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(played_at_ms / 1000))


@dataclass
class MoveFact:
    game_id: str
    ply: int
    color: str
    phase: str
    endgame_type: str | None     # set only for endgame-phase moves
    family: str
    band: str
    gap: str | None
    month: str
    pawn_loss: float         # evaluation the mover gave away on this move (>= 0)
    judgment: str | None     # None = fine move (or discarded mistake)
    motif: str | None


def build_move_facts(conn: sqlite3.Connection) -> list[MoveFact]:
    mistakes = {
        (r["game_id"], r["ply"]): r for r in conn.execute(
            "SELECT game_id, ply, judgment, motif, discarded_at FROM mistakes")
    }
    rows = conn.execute(
        """SELECT e.game_id, e.ply, e.fen, e.score_cp, e.score_mate,
                  g.color, g.opening, g.user_rating, g.opponent_rating,
                  g.played_at, g.division_middle, g.division_end
           FROM evals e JOIN games g ON g.id = e.game_id
           WHERE g.variant = 'standard'
           ORDER BY e.game_id, e.ply"""
    ).fetchall()

    facts: list[MoveFact] = []
    for before, after in zip(rows, rows[1:]):
        if before["game_id"] != after["game_id"]:
            continue
        ply = before["ply"]
        is_white = before["color"] == "white"
        if (ply % 2 == 0) != is_white:
            continue  # opponent's move
        cp_before = judgments.score_to_cp(before["score_cp"], before["score_mate"])
        cp_after = judgments.score_to_cp(after["score_cp"], after["score_mate"])
        m = mistakes.get((before["game_id"], ply))
        counted = m is not None and m["discarded_at"] is None
        phase = _phase_from_fen(before["fen"], ply,
                                before["division_middle"], before["division_end"])
        facts.append(MoveFact(
            game_id=before["game_id"],
            ply=ply,
            color=before["color"],
            phase=phase,
            endgame_type=classify_endgame(before["fen"]) if phase == "endgame" else None,
            family=_family(before["opening"]),
            band=_band(ply // 2 + 1),
            gap=_gap_band(before["user_rating"], before["opponent_rating"]),
            month=_month(before["played_at"]),
            pawn_loss=_pawn_loss(cp_before, cp_after, is_white),
            judgment=m["judgment"] if counted else None,
            motif=(m["motif"] or "positional") if counted else None,
        ))
    return facts


class _Bucket:
    __slots__ = ("n_moves", "n_mistakes", "n_blunders", "sum_pawn_loss")

    def __init__(self):
        self.n_moves = 0
        self.n_mistakes = 0
        self.n_blunders = 0
        self.sum_pawn_loss = 0.0

    def add(self, fact: MoveFact) -> None:
        self.n_moves += 1
        self.sum_pawn_loss += fact.pawn_loss
        if fact.judgment is not None:
            self.n_mistakes += 1
            if fact.judgment == "blunder":
                self.n_blunders += 1

    def stats(self) -> dict:
        n = self.n_moves or 1
        return {
            "n_moves": self.n_moves,
            "n_mistakes": self.n_mistakes,
            "n_blunders": self.n_blunders,
            "per100": round(100 * self.n_mistakes / n, 2),
            "blunders_per100": round(100 * self.n_blunders / n, 2),
            "avg_pawn_loss": round(self.sum_pawn_loss / n, 2),
        }


def _group(facts: list[MoveFact], key) -> dict:
    out: dict = {}
    for f in facts:
        k = key(f)
        if k is None:
            continue
        out.setdefault(k, _Bucket()).add(f)
    return out


def _train_url(**params) -> str:
    q = {k: v for k, v in params.items() if v}
    q["judgments"] = ",".join(DRILL_JUDGMENTS)
    q["focus"] = 1
    # Relative on purpose: identical here, and required by the static build,
    # which is served from a GitHub Pages subpath (/chess-trainer/).
    return "train.html?" + urlencode(q)


def _available(conn, *, phases: str | None = None, color: str | None = None,
               opening: str | None = None, motif: str | None = None,
               endgame: str | None = None) -> int:
    """Non-discarded blunder/mistake puzzles matching a training URL."""
    where = ["m.discarded_at IS NULL",
             f"m.judgment IN ({','.join('?' * len(DRILL_JUDGMENTS))})"]
    params: list = list(DRILL_JUDGMENTS)
    join = ""
    if endgame:
        phases = "endgame"
    if phases:
        where.append("m.phase = ?")
        params.append(phases)
    if color:
        where.append("m.color = ?")
        params.append(color)
    if opening:
        join = "JOIN games g ON g.id = m.game_id"
        where.append("(g.opening = ? OR g.opening LIKE ? || ':%')")
        params += [opening, opening]
    if motif:
        if motif == "positional":
            where.append("(m.motif = 'positional' OR m.motif IS NULL)")
        else:
            where.append("m.motif = ?")
            params.append(motif)
    if endgame:
        rows = conn.execute(
            f"SELECT m.fen FROM mistakes m {join} WHERE {' AND '.join(where)}",
            params).fetchall()
        return sum(1 for r in rows if classify_endgame(r["fen"]) == endgame)
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM mistakes m {join} WHERE {' AND '.join(where)}",
        params).fetchone()["n"]


def _signal(dimension: str, key: str, bucket: _Bucket, overall: _Bucket,
            training: dict | None) -> dict | None:
    p_o = overall.n_mistakes / overall.n_moves
    if p_o <= 0 or p_o >= 1:
        return None
    p_b = bucket.n_mistakes / bucket.n_moves
    se = math.sqrt(p_o * (1 - p_o) / bucket.n_moves)
    z = (p_b - p_o) / se
    return {
        "dimension": dimension,
        "key": key,
        "z": round(z, 2),
        "ratio": round(p_b / p_o, 2) if p_o else None,
        **bucket.stats(),
        "baseline_per100": round(100 * p_o, 2),
        "training": training,
    }


def _load_report() -> dict | None:
    try:
        with open(REPORT_PATH, encoding="utf-8") as fh:
            report = json.load(fh)
        # Reports are hand-authored and historically used "/train.html?...".
        # Normalize to relative so the static build works from a subpath.
        for verdict in report.get("verdicts") or []:
            training = verdict.get("training")
            if training and isinstance(training.get("url"), str):
                training["url"] = training["url"].lstrip("/")
        return report
    except (OSError, ValueError):
        return None


def _report_probe() -> tuple:
    try:
        st = REPORT_PATH.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (None, None)


_cache: tuple[tuple, dict] | None = None


def compute_insights(conn: sqlite3.Connection, force: bool = False) -> dict:
    global _cache
    probe = tuple(conn.execute(
        """SELECT (SELECT COUNT(*) FROM games),
                  (SELECT MAX(analyzed_at) FROM games),
                  (SELECT COUNT(*) FROM mistakes),
                  (SELECT COUNT(*) FROM mistakes WHERE discarded_at IS NOT NULL),
                  (SELECT COUNT(*) FROM mistakes WHERE motif IS NOT NULL)"""
    ).fetchone()) + _report_probe()
    if not force and _cache is not None and _cache[0] == probe:
        return _cache[1]

    facts = build_move_facts(conn)
    games = conn.execute(
        """SELECT id, color, opening, result, status, user_rating, opponent_rating,
                  played_at
           FROM games WHERE variant = 'standard' AND analyzed_at IS NOT NULL"""
    ).fetchall()

    overall = _Bucket()
    for f in facts:
        overall.add(f)

    by_color = _group(facts, lambda f: f.color)
    by_phase = _group(facts, lambda f: f.phase)
    by_phase_color = _group(facts, lambda f: (f.phase, f.color))
    by_endgame = _group(facts, lambda f: f.endgame_type)
    by_opening = _group(facts, lambda f: (f.family, f.color))
    by_band = _group(facts, lambda f: f.band)
    by_gap = _group(facts, lambda f: f.gap)
    by_month = _group(facts, lambda f: f.month)

    # Game-level aggregates.
    def score_pct(rows) -> float | None:
        if not rows:
            return None
        pts = sum(1.0 if g["result"] == "win" else 0.5 if g["result"] == "draw" else 0.0
                  for g in rows)
        return round(100 * pts / len(rows), 1)

    games_by_opening: dict = {}
    games_by_gap: dict = {}
    games_by_month: dict = {}
    for g in games:
        games_by_opening.setdefault((_family(g["opening"]), g["color"]), []).append(g)
        gap = _gap_band(g["user_rating"], g["opponent_rating"])
        if gap:
            games_by_gap.setdefault(gap, []).append(g)
        games_by_month.setdefault(_month(g["played_at"]), []).append(g)

    losses = [g for g in games if g["result"] == "loss"]
    forfeit_losses = [g for g in losses if "time" in (g["status"] or "").lower()]

    # Signals: buckets big enough to compare against the global rate.
    signals: list[dict] = []
    for color, b in by_color.items():
        if b.n_moves >= MIN_MOVES["color"]:
            s = _signal("color", color, b, overall,
                        {"label": f"Drill mistakes as {color.title()}",
                         "url": _train_url(color=color),
                         "available": _available(conn, color=color)})
            if s:
                signals.append(s)
    for phase, b in by_phase.items():
        if b.n_moves >= MIN_MOVES["phase"]:
            s = _signal("phase", phase, b, overall,
                        {"label": f"Drill {phase} mistakes",
                         "url": _train_url(phases=phase),
                         "available": _available(conn, phases=phase)})
            if s:
                signals.append(s)
    for (phase, color), b in by_phase_color.items():
        if b.n_moves >= MIN_MOVES["phase_color"]:
            s = _signal("phase_color", f"{phase}|{color}", b, overall,
                        {"label": f"Drill {phase} mistakes as {color.title()}",
                         "url": _train_url(phases=phase, color=color),
                         "available": _available(conn, phases=phase, color=color)})
            if s:
                signals.append(s)
    for (family, color), b in by_opening.items():
        n_games = len(games_by_opening.get((family, color), []))
        if b.n_moves >= MIN_MOVES["opening"] and n_games >= MIN_GAMES_OPENING:
            s = _signal("opening", f"{family}|{color}", b, overall,
                        {"label": f"Drill mistakes from your {family} games",
                         "url": _train_url(opening=family, color=color),
                         "available": _available(conn, opening=family, color=color)})
            if s:
                s["games"] = n_games
                s["score_pct"] = score_pct(games_by_opening[(family, color)])
                signals.append(s)
    for eg_type, b in by_endgame.items():
        if b.n_moves >= MIN_MOVES["endgame"]:
            s = _signal("endgame", eg_type, b, overall,
                        {"label": f"Drill {eg_type} endgame mistakes",
                         "url": _train_url(endgame=eg_type),
                         "available": _available(conn, endgame=eg_type)})
            if s:
                signals.append(s)
    for band, b in by_band.items():
        if b.n_moves >= MIN_MOVES["band"]:
            s = _signal("band", band, b, overall, None)
            if s:
                signals.append(s)
    for gap, b in by_gap.items():
        if b.n_moves >= MIN_MOVES["gap"]:
            s = _signal("gap", gap, b, overall, None)
            if s:
                s["games"] = len(games_by_gap.get(gap, []))
                s["score_pct"] = score_pct(games_by_gap.get(gap, []))
                signals.append(s)
    signals.sort(key=lambda s: abs(s["z"]), reverse=True)

    # Motifs are mistake-conditional: shares of mistakes, not move rates.
    motif_counts: dict[str, list[float]] = {}
    for f in facts:
        if f.motif is not None:
            motif_counts.setdefault(f.motif, []).append(f.pawn_loss)
    motifs = [{
        "motif": motif,
        "n": len(wl),
        "share": round(100 * len(wl) / max(1, overall.n_mistakes), 1),
        "avg_pawn_loss": round(sum(wl) / len(wl), 2),
        "training": {"label": f"Drill {motif.replace('_', ' ')} mistakes",
                     "url": _train_url(motif=motif),
                     "available": _available(conn, motif=motif)},
    } for motif, wl in sorted(motif_counts.items(), key=lambda kv: -len(kv[1]))]

    openings = []
    for (family, color), b in sorted(by_opening.items(),
                                     key=lambda kv: -kv[1].n_moves):
        rows = games_by_opening.get((family, color), [])
        if len(rows) < 3:
            continue
        openings.append({
            "family": family, "color": color, "games": len(rows),
            "score_pct": score_pct(rows), **b.stats(),
            # "from your X games", not "X mistakes": this spans the whole game,
            # and calling it an opening drill is what made it read as the phase.
            "training": {"label": f"Drill mistakes from your {family} games",
                         "url": _train_url(opening=family, color=color),
                         "available": _available(conn, opening=family, color=color)},
        })

    trend = [{
        "month": month, **by_month[month].stats(),
        "games": len(games_by_month.get(month, [])),
        "avg_opponent_rating": round(sum(g["opponent_rating"] or 0 for g in
                                         games_by_month.get(month, [])) /
                                     max(1, len(games_by_month.get(month, [])))),
    } for month in sorted(by_month)]

    report = _load_report()
    report_stale = False
    if report:
        base = report.get("based_on", {})
        n_games, n_mistakes = len(games), overall.n_mistakes
        report_stale = (
            abs(n_games - base.get("games", 0)) > 0.10 * max(1, base.get("games", 1))
            or abs(n_mistakes - base.get("mistakes", 0))
            > 0.10 * max(1, base.get("mistakes", 1)))

    payload = {
        "sample": {
            "games": len(games),
            "own_moves": overall.n_moves,
            "mistakes": overall.n_mistakes,
            "date_range": [min((g["played_at"] for g in games), default=None),
                           max((g["played_at"] for g in games), default=None)],
        },
        "overall": overall.stats(),
        "signals": signals,
        "sections": {
            "phase_color": [
                {"phase": phase, "color": color, **b.stats()}
                for (phase, color), b in sorted(by_phase_color.items())],
            "endgames": [
                {"type": eg_type, **b.stats(),
                 "training": {"label": f"Drill {eg_type} endgame mistakes",
                              "url": _train_url(endgame=eg_type),
                              "available": _available(conn, endgame=eg_type)}}
                for eg_type, b in sorted(by_endgame.items(),
                                         key=lambda kv: -kv[1].n_moves)],
            "openings": openings,
            "motifs": motifs,
            "bands": [{"band": band, **by_band[band].stats()}
                      for _, band in BANDS if band in by_band],
            "gap": [{"gap": gap, **by_gap[gap].stats(),
                     "games": len(games_by_gap.get(gap, [])),
                     "score_pct": score_pct(games_by_gap.get(gap, []))}
                    for gap in ("weaker", "similar", "stronger") if gap in by_gap],
            "trend": trend,
            "misc": {
                "wins": sum(1 for g in games if g["result"] == "win"),
                "losses": len(losses),
                "draws": sum(1 for g in games if g["result"] == "draw"),
                "time_forfeit_losses": len(forfeit_losses),
                "forfeit_pct": round(100 * len(forfeit_losses) / len(losses), 1)
                if losses else None,
            },
        },
        "report": report,
        "report_stale": report_stale,
    }
    _cache = (probe, payload)
    return payload
