"""FastAPI app: dashboard stats + trainer API with spaced repetition."""

import asyncio
import sys
import threading

import chess
import chess.engine
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import PROJECT_ROOT, db, engine as eng, insights, judgments, load_config, srs

app = FastAPI(title="Chess Trainer")
config = load_config()


@app.middleware("http")
async def no_cache_static(request, call_next):
    # Local single-user app: always serve fresh frontend files.
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        if sys.platform == "win32":
            # Uvicorn installs a SelectorEventLoop policy on Windows, under which
            # python-chess's engine thread cannot spawn the Stockfish subprocess.
            asyncio.set_event_loop_policy(chess.engine.EventLoopPolicy())
        _engine = eng.create(config)
    return _engine


def _conn():
    return db.connect()


@app.on_event("shutdown")
def _shutdown():
    global _engine
    if _engine is not None:
        _engine.quit()
        _engine = None


@app.get("/api/stats")
def stats():
    conn = _conn()
    now = db.now_s()
    games = conn.execute(
        "SELECT COUNT(*) AS total, COUNT(analyzed_at) AS analyzed FROM games"
    ).fetchone()
    by_judgment = {
        r["judgment"]: r["n"] for r in conn.execute(
            "SELECT judgment, COUNT(*) AS n FROM mistakes "
            "WHERE discarded_at IS NULL GROUP BY judgment")
    }
    by_phase = [dict(r) for r in conn.execute(
        "SELECT phase, judgment, COUNT(*) AS n FROM mistakes "
        "WHERE discarded_at IS NULL GROUP BY phase, judgment")]
    by_color = [dict(r) for r in conn.execute(
        "SELECT color, judgment, COUNT(*) AS n FROM mistakes "
        "WHERE discarded_at IS NULL GROUP BY color, judgment")]
    due = conn.execute(
        """SELECT COUNT(*) AS n FROM mistakes m
           LEFT JOIN scheduling s ON s.mistake_id = m.id
           WHERE m.discarded_at IS NULL AND (s.due_at IS NULL OR s.due_at <= ?)""",
        (now,),
    ).fetchone()["n"]
    new = conn.execute(
        """SELECT COUNT(*) AS n FROM mistakes m
           LEFT JOIN scheduling s ON s.mistake_id = m.id
           WHERE m.discarded_at IS NULL AND s.mistake_id IS NULL""",
    ).fetchone()["n"]
    attempts = conn.execute("SELECT COUNT(*) AS n FROM attempts").fetchone()["n"]
    return {
        "username": config["lichess"]["username"],
        "games": {"total": games["total"], "analyzed": games["analyzed"],
                  "pending": games["total"] - games["analyzed"]},
        "mistakes": by_judgment,
        "by_phase": by_phase,
        "by_color": by_color,
        "due": due, "new": new, "attempts": attempts,
    }


def _dests(board: chess.Board) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for move in board.legal_moves:
        out.setdefault(chess.square_name(move.from_square), []).append(
            chess.square_name(move.to_square))
        if board.is_castling(move):
            # Lichess-style gesture: also allow dropping the king on the rook.
            rook_file = 7 if move.to_square > move.from_square else 0
            rook_sq = chess.square(rook_file, chess.square_rank(move.from_square))
            out[chess.square_name(move.from_square)].append(chess.square_name(rook_sq))
    return out


def _parse_move(board: chess.Board, uci: str) -> chess.Move | None:
    """Parse a UCI move; maps the king-onto-rook castling gesture to the real move."""
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    if move in board.legal_moves:
        return move
    piece = board.piece_at(move.from_square)
    target = board.piece_at(move.to_square)
    if (piece and piece.piece_type == chess.KING and target
            and target.piece_type == chess.ROOK and target.color == piece.color):
        file = 6 if move.to_square > move.from_square else 2
        castle = chess.Move(move.from_square,
                            chess.square(file, chess.square_rank(move.from_square)))
        if castle in board.legal_moves:
            return castle
    return None


@app.get("/api/next")
def next_puzzle(judgments_filter: str | None = None, color: str | None = None,
                phases: str | None = None, opening: str | None = None,
                motif: str | None = None, endgame: str | None = None,
                focus: int = 0):
    conn = _conn()
    now = db.now_s()
    where = ["m.discarded_at IS NULL", "(s.due_at IS NULL OR s.due_at <= ?)"]
    params: list = [now]
    if judgments_filter:
        kinds = [j.strip() for j in judgments_filter.split(",") if j.strip()]
        where.append(f"m.judgment IN ({','.join('?' * len(kinds))})")
        params += kinds
    if phases:
        wanted = [p.strip() for p in phases.split(",")
                  if p.strip() in ("opening", "middlegame", "endgame")]
        if wanted:
            where.append(f"m.phase IN ({','.join('?' * len(wanted))})")
            params += wanted
    if color in ("white", "black"):
        where.append("m.color = ?")
        params.append(color)
    if opening:
        # Family match: exact name or any "Family: Variation" of it.
        where.append("(g.opening = ? OR g.opening LIKE ? || ':%')")
        params += [opening, opening]
    if motif:
        kinds = [m.strip() for m in motif.split(",") if m.strip()]
        clause = f"m.motif IN ({','.join('?' * len(kinds))})"
        if "positional" in kinds:
            clause = f"({clause} OR m.motif IS NULL)"  # pre-backfill rows
        where.append(clause)
        params += kinds
    if endgame:
        # Endgame type isn't stored; classify candidate FENs in Python below.
        where.append("m.phase = 'endgame'")
    base_query = f"""FROM mistakes m
            LEFT JOIN scheduling s ON s.mistake_id = m.id
            JOIN games g ON g.id = m.game_id
            WHERE {' AND '.join(where)}"""
    if focus or endgame:
        candidates = conn.execute(
            f"SELECT m.id, m.fen, m.win_loss, COALESCE(s.lapses, 0) AS lapses {base_query}",
            params).fetchall()
        if endgame:
            wanted = [e.strip() for e in endgame.split(",") if e.strip()]
            candidates = [c for c in candidates
                          if insights.classify_endgame(c["fen"]) in wanted]
        row = None
        if candidates:
            import random
            if focus:
                # Weighted pick: prioritize costly mistakes and (once training
                # data exists) puzzles that keep lapsing.
                picked = random.choices(
                    candidates,
                    weights=[c["win_loss"] + 15 * c["lapses"] for c in candidates])[0]
            else:
                picked = random.choice(candidates)
            row = conn.execute(
                """SELECT m.*, g.opponent, g.speed, g.played_at, g.user_rating,
                          g.opponent_rating, g.opening
                   FROM mistakes m JOIN games g ON g.id = m.game_id
                   WHERE m.id = ?""", (picked["id"],)).fetchone()
    else:
        row = conn.execute(
            f"""SELECT m.*, g.opponent, g.speed, g.played_at, g.user_rating,
                       g.opponent_rating, g.opening
                {base_query}
                ORDER BY RANDOM() LIMIT 1""",
            params,
        ).fetchone()
    if row is None:
        soonest = conn.execute(
            """SELECT MIN(s.due_at) AS t FROM mistakes m
               JOIN scheduling s ON s.mistake_id = m.id
               WHERE m.discarded_at IS NULL""").fetchone()["t"]
        return {"puzzle": None, "next_due_in_s": (soonest - now) if soonest else None}

    board = chess.Board(row["fen"])
    ply = row["ply"]
    return {"puzzle": {
        "id": row["id"],
        "pid": f"{row['game_id']}:{ply}",
        "fen": row["fen"],
        "color": row["color"],
        "played_san": row["played_san"],
        "played_uci": row["played_uci"],
        "judgment": row["judgment"],
        "phase": row["phase"],
        "motif": row["motif"],
        "opening": row["opening"],
        "move_number": ply // 2 + 1,
        "dests": _dests(board),
        "game": {
            "id": row["game_id"],
            "url": f"https://lichess.org/{row['game_id']}",
            "opponent": row["opponent"],
            "speed": row["speed"],
            "played_at": row["played_at"],
            "user_rating": row["user_rating"],
            "opponent_rating": row["opponent_rating"],
        },
    }}


def _resolve_id(conn, mistake_id: int | None, pid: str | None) -> int:
    """Accept either the autoincrement id or the stable "<game_id>:<ply>" pid.

    mistakes.id is rebuilt by re-analysis, so anything that has to survive a
    rebuild (the static build's local training state) keys off pid instead.
    """
    if mistake_id is not None:
        return mistake_id
    if not pid or ":" not in pid:
        raise HTTPException(400, "mistake_id or pid required")
    game_id, _, ply = pid.rpartition(":")
    row = conn.execute(
        "SELECT id FROM mistakes WHERE game_id = ? AND ply = ?", (game_id, ply)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "unknown puzzle")
    return row["id"]


class Attempt(BaseModel):
    mistake_id: int | None = None
    pid: str | None = None  # stable "<game_id>:<ply>" alternative to mistake_id
    move_uci: str | None = None  # None = gave up
    took_ms: int | None = None
    retry: bool = False  # later tries after a fail: logged, but no SRS update


@app.post("/api/attempt")
def attempt(a: Attempt):
    conn = _conn()
    mistake_id = _resolve_id(conn, a.mistake_id, a.pid)
    row = conn.execute("SELECT * FROM mistakes WHERE id = ?", (mistake_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "unknown puzzle")

    board = chess.Board(row["fen"])
    user_color = board.turn
    attempt_win = None
    attempt_eval_str = None
    played_san = None

    if a.move_uci is None:
        correct = False
    elif a.move_uci == row["best_uci"]:
        correct = True
        played_san = row["best_san"]
    else:
        move = _parse_move(board, a.move_uci)
        if move is None:
            raise HTTPException(400, "illegal move")
        if move.uci() == row["best_uci"]:
            correct = True
            played_san = row["best_san"]
        else:
            played_san = board.san(move)
            after = board.copy()
            after.push(move)
            with _engine_lock:
                ev = eng.evaluate(_get_engine(), after, config["trainer"]["check_nodes"])
            cp = judgments.score_to_cp(ev.score_cp, ev.score_mate)
            attempt_win = judgments.win_pct(cp, user_color)
            attempt_eval_str = judgments.fmt_eval(ev.score_cp, ev.score_mate, user_color)
            correct = attempt_win >= row["win_before"] - config["trainer"]["tolerance_winpct"]

    now = db.now_s()
    conn.execute(
        "INSERT INTO attempts (mistake_id, attempted_at, move_uci, correct, took_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (mistake_id, now, a.move_uci, int(correct), a.took_ms),
    )
    # The first try decides the spaced-repetition outcome; retries are practice.
    sched = None if a.retry else srs.review(conn, mistake_id, correct, now)
    conn.commit()
    ev_best = conn.execute(
        "SELECT score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
        (row["game_id"], row["ply"])).fetchone()
    eval_best = (judgments.fmt_eval(ev_best["score_cp"], ev_best["score_mate"], user_color)
                 if ev_best else None)
    return {
        "correct": correct,
        "your_move_san": played_san,
        "your_move_win_pct": round(attempt_win, 1) if attempt_win is not None else None,
        "eval_yours": attempt_eval_str,
        "eval_best": eval_best,
        "win_best": row["win_before"],
        "best_san": row["best_san"],
        "best_uci": row["best_uci"],
        "pv_san": row["pv_san"],
        "explanation": row["explanation"],
        "explanation_played": row["explanation_played"],
        "game_played_san": row["played_san"],
        "win_loss": row["win_loss"],
        "scheduling": sched,
    }


class EvalReq(BaseModel):
    fen: str
    move_uci: str | None = None  # optional move to apply to fen first


@app.post("/api/eval")
def eval_position(req: EvalReq):
    """Free exploration: evaluate a position (optionally after a move)."""
    try:
        board = chess.Board(req.fen)
    except ValueError:
        raise HTTPException(400, "bad fen")
    played_san = None
    if req.move_uci:
        move = _parse_move(board, req.move_uci)
        if move is None:
            raise HTTPException(400, "illegal move")
        played_san = board.san(move)
        board.push(move)

    if board.is_game_over():
        outcome = board.outcome()
        winner = None
        if outcome and outcome.winner is not None:
            winner = "white" if outcome.winner else "black"
            result = f"checkmate — {winner} wins"
        else:
            result = "draw"
        return {"fen": board.fen(), "turn": "white" if board.turn else "black",
                "dests": {}, "played_san": played_san, "game_over": result,
                "winner": winner, "lines": []}

    from .analysis import _san_line
    with _engine_lock:
        evs = eng.evaluate_multi(_get_engine(), board,
                                 config["trainer"]["check_nodes"], multipv=2)
    lines = []
    for ev in evs:
        cp = judgments.score_to_cp(ev.score_cp, ev.score_mate)
        pv_san = _san_line(board, ev.pv_uci, 12)
        lines.append({
            "cp": ev.score_cp,
            "mate": ev.score_mate,
            "win_white": round(judgments.win_pct(cp, chess.WHITE), 1),
            "first_uci": ev.best_uci,
            "pv_san": pv_san,
        })
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn else "black",
        "dests": _dests(board),
        "played_san": played_san,
        "game_over": None,
        "winner": None,
        "lines": lines,
    }


class Discard(BaseModel):
    mistake_id: int | None = None
    pid: str | None = None


@app.post("/api/discard")
def discard(d: Discard):
    conn = _conn()
    cur = conn.execute(
        "UPDATE mistakes SET discarded_at = ? WHERE id = ? AND discarded_at IS NULL",
        (db.now_s(), _resolve_id(conn, d.mistake_id, d.pid)),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "unknown or already discarded puzzle")
    return {"ok": True}


class ResetTraining(BaseModel):
    confirm: bool = False


@app.post("/api/reset-training")
def reset_training(r: ResetTraining):
    """Wipe attempts + spaced-repetition state. Puzzles are kept."""
    if not r.confirm:
        raise HTTPException(400, "confirm required")
    conn = _conn()
    result = srs.reset_training(conn)
    conn.commit()
    return {"ok": True, **result}


@app.get("/api/insights")
def insights_stats(refresh: int = 0):
    """Strength/weakness stats + the Claude-authored coach report if present."""
    return insights.compute_insights(_conn(), force=bool(refresh))


app.mount("/", StaticFiles(directory=PROJECT_ROOT / "web", html=True), name="static")
