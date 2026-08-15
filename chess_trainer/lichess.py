"""Download games from the public Lichess API (no credentials required)."""

import json
import sqlite3
import sys
import time

import requests

from . import db

API_URL = "https://lichess.org/api/games/user/{username}"
USER_URL = "https://lichess.org/api/user/{username}"
# Lichess serves 404/429 to anonymous generic clients; identify ourselves.
USER_AGENT = "chess-trainer/0.1 (personal training tool; pedro@functions.engineering)"

BACKOFFS_S = [65, 120, 180, 300, 600]
COMMIT_EVERY = 20
PROGRESS_EVERY = 20


class _RateLimited(Exception):
    def __init__(self, retry_after: int | None):
        self.retry_after = retry_after


def _parse_game(line: str, username: str) -> dict | None:
    g = json.loads(line)
    players = g.get("players", {})

    def player_id(side: str) -> str:
        return (players.get(side, {}).get("user") or {}).get("id", "")

    uname = username.lower()
    if player_id("white") == uname:
        color = "white"
    elif player_id("black") == uname:
        color = "black"
    else:
        return None  # user not in this game (shouldn't happen)

    opp_side = "black" if color == "white" else "white"
    opp = players.get(opp_side, {})
    me = players.get(color, {})

    winner = g.get("winner")
    if winner is None:
        result = "draw" if g.get("status") in ("draw", "stalemate") else "unknown"
    else:
        result = "win" if winner == color else "loss"

    division = g.get("division", {})
    return {
        "id": g["id"],
        "played_at": g.get("createdAt", 0),
        "speed": g.get("speed"),
        "perf": g.get("perf"),
        "rated": 1 if g.get("rated") else 0,
        "variant": g.get("variant", "standard"),
        "color": color,
        "opponent": (opp.get("user") or {}).get("name") or opp.get("aiLevel") and f"Stockfish level {opp['aiLevel']}" or "Anonymous",
        "opponent_rating": opp.get("rating"),
        "user_rating": me.get("rating"),
        "result": result,
        "status": g.get("status"),
        "opening": (g.get("opening") or {}).get("name"),
        "moves": g.get("moves", ""),
        "division_middle": division.get("middle"),
        "division_end": division.get("end"),
        "raw": line,
    }


def _insert_game(conn: sqlite3.Connection, game: dict) -> int:
    cur = conn.execute(
        """INSERT INTO games (id, played_at, speed, perf, rated, variant, color,
                              opponent, opponent_rating, user_rating, result, status,
                              opening, moves, division_middle, division_end, raw)
           VALUES (:id, :played_at, :speed, :perf, :rated, :variant, :color,
                   :opponent, :opponent_rating, :user_rating, :result, :status,
                   :opening, :moves, :division_middle, :division_end, :raw)
           ON CONFLICT(id) DO NOTHING""",
        game,
    )
    return cur.rowcount


def _fetch_total(username: str) -> int | None:
    try:
        r = requests.get(
            USER_URL.format(username=username),
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.ok:
            return r.json().get("count", {}).get("all")
    except requests.RequestException:
        pass
    return None


def _progress(done: int, total: int | None, width: int = 30) -> None:
    if total:
        frac = min(1.0, done / total)
        filled = int(frac * width)
        msg = f"[{'#' * filled}{'-' * (width - filled)}] {done}/{total} games ({frac * 100:.0f}%)"
    else:
        msg = f"{done} games downloaded"
    if sys.stdout.isatty():
        print("\r" + msg, end="", flush=True)
    else:
        print(msg, flush=True)


def import_file(conn: sqlite3.Connection, username: str, path: str) -> tuple[int, int]:
    """Import games from a manually downloaded export file (NDJSON or PGN)."""
    with open(path, encoding="utf-8") as f:
        head = f.read(200).lstrip()
    if head.startswith("["):
        return _import_pgn(conn, username, path)
    return _import_ndjson(conn, username, path)


def _import_ndjson(conn: sqlite3.Connection, username: str, path: str) -> tuple[int, int]:
    new = seen = 0
    max_created = int(db.get_meta(conn, "last_sync_created_at") or 0)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            seen += 1
            game = _parse_game(line, username)
            if game is None:
                continue
            max_created = max(max_created, game["played_at"])
            new += _insert_game(conn, game)
    if max_created:
        db.set_meta(conn, "last_sync_created_at", str(max_created))
    conn.commit()
    return new, seen


def _import_pgn(conn: sqlite3.Connection, username: str, path: str) -> tuple[int, int]:
    import datetime

    import chess.pgn

    new = seen = 0
    max_created = int(db.get_meta(conn, "last_sync_created_at") or 0)
    uname = username.lower()
    with open(path, encoding="utf-8") as f:
        while True:
            game = chess.pgn.read_game(f)
            if game is None:
                break
            seen += 1
            h = game.headers
            white = h.get("White", "")
            black = h.get("Black", "")
            if white.lower() == uname:
                color, opp_name = "white", black
            elif black.lower() == uname:
                color, opp_name = "black", white
            else:
                continue

            game_id = h.get("Site", "").rstrip("/").rsplit("/", 1)[-1] or None
            if not game_id:
                continue

            try:
                dt = datetime.datetime.strptime(
                    f"{h.get('UTCDate', '????')} {h.get('UTCTime', '00:00:00')}",
                    "%Y.%m.%d %H:%M:%S",
                ).replace(tzinfo=datetime.timezone.utc)
                played_at = int(dt.timestamp() * 1000)
            except ValueError:
                played_at = 0

            event = h.get("Event", "").lower()
            speed = next(
                (s for s in ("bullet", "blitz", "rapid", "classical", "correspondence")
                 if s in event),
                None,
            )

            pgn_result = h.get("Result", "*")
            if pgn_result == "1/2-1/2":
                result = "draw"
            elif pgn_result in ("1-0", "0-1"):
                won_by_white = pgn_result == "1-0"
                result = "win" if (color == "white") == won_by_white else "loss"
            else:
                result = "unknown"

            board = game.board()
            sans = []
            for mv in game.mainline_moves():
                sans.append(board.san(mv))
                board.push(mv)

            def rating(header: str) -> int | None:
                v = h.get(header, "")
                return int(v) if v.isdigit() else None

            row = {
                "id": game_id,
                "played_at": played_at,
                "speed": speed,
                "perf": speed,
                "rated": 1 if "rated" in event else 0,
                "variant": h.get("Variant", "Standard").lower(),
                "color": color,
                "opponent": opp_name or "Anonymous",
                "opponent_rating": rating("BlackElo" if color == "white" else "WhiteElo"),
                "user_rating": rating("WhiteElo" if color == "white" else "BlackElo"),
                "result": result,
                "status": h.get("Termination", "").lower() or None,
                "opening": h.get("Opening"),
                "moves": " ".join(sans),
                "division_middle": None,
                "division_end": None,
                "raw": str(game),
            }
            max_created = max(max_created, played_at)
            new += _insert_game(conn, row)
    if max_created:
        db.set_meta(conn, "last_sync_created_at", str(max_created))
    conn.commit()
    return new, seen


def sync(conn: sqlite3.Connection, username: str) -> tuple[int, int]:
    """Stream games oldest-first with a progress bar; resumable and retried.

    Games are committed every COMMIT_EVERY rows together with the sync cursor,
    so an interrupted or rate-limited download resumes where it left off.
    """
    total = _fetch_total(username)
    in_db = conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]
    done = in_db
    new = seen = 0
    headers = {"Accept": "application/x-ndjson", "User-Agent": USER_AGENT}

    attempt = 0
    while True:
        last = db.get_meta(conn, "last_sync_created_at")
        params = {"moves": "true", "opening": "true", "division": "true",
                  "sort": "dateAsc"}
        if last:
            params["since"] = int(last) + 1
        try:
            resp = requests.get(
                API_URL.format(username=username),
                params=params, headers=headers, stream=True, timeout=120,
            )
            if resp.status_code == 429:
                raise _RateLimited(int(resp.headers.get("Retry-After", 0)) or None)
            resp.raise_for_status()

            uncommitted = 0
            max_created = int(last) if last else 0
            _progress(done, total)
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                seen += 1
                game = _parse_game(line, username)
                if game is None:
                    continue
                max_created = max(max_created, game["played_at"])
                inserted = _insert_game(conn, game)
                new += inserted
                done += inserted
                uncommitted += 1
                if uncommitted >= COMMIT_EVERY:
                    db.set_meta(conn, "last_sync_created_at", str(max_created))
                    conn.commit()
                    uncommitted = 0
                if done % PROGRESS_EVERY == 0:
                    _progress(done, total)
            if max_created:
                db.set_meta(conn, "last_sync_created_at", str(max_created))
            conn.commit()
            _progress(done, total)
            if sys.stdout.isatty():
                print()
            return new, seen

        except (_RateLimited, requests.RequestException) as e:
            conn.commit()
            if attempt >= len(BACKOFFS_S):
                print("Giving up after too many retries.", flush=True)
                raise
            wait = (e.retry_after if isinstance(e, _RateLimited) and e.retry_after
                    else BACKOFFS_S[attempt])
            reason = ("rate limited by Lichess" if isinstance(e, _RateLimited)
                      else f"connection problem ({type(e).__name__})")
            print(f"Sync interrupted: {reason}; retrying in {wait}s "
                  f"(attempt {attempt + 1}/{len(BACKOFFS_S)}, {done} games saved so far)",
                  flush=True)
            time.sleep(wait)
            attempt += 1
