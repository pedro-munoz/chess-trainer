"""Precomputed acceptable-move sets, so answers can be judged without an engine.

`web.attempt` accepts a move that isn't the engine's first choice when it keeps
the mover's win% within `tolerance_winpct` of `win_before`. That check needs a
live search, which the static build has no cheap way to run: a wasm engine at
phone-affordable node counts is a *different* engine, so the same move would be
accepted on the laptop and rejected on the phone.

This module runs that check ahead of time, here, with the same engine and the
same node budget, and caches the verdicts. The client then only has to look a
move up in a list.

The cache is keyed by (game_id, ply) — not mistakes.id, which re-analysis
rebuilds — and carries the FEN it was computed for, so a puzzle that changes
underneath the same key is recomputed rather than silently reused.
"""

import json

import chess

from . import db, engine as eng, judgments, load_config

MULTIPV = 10


def _pov_win(ev: eng.Evaluation, pov: chess.Color) -> float:
    return judgments.win_pct(judgments.score_to_cp(ev.score_cp, ev.score_mate), pov)


def compute_one(engine, fen: str, win_before: float, nodes: int,
                tolerance: float, multipv: int = MULTIPV) -> tuple[list, bool]:
    """Return (moves, complete) for one puzzle position.

    `moves` is [[uci, cp, mate], ...] — the acceptable alternatives, best first,
    with white-relative scores of the position *after* the move (the same
    numbers `web.attempt` feeds to `judgments.fmt_eval`).

    `complete` is True when at least one candidate was rejected: the candidates
    are searched best-first, so a rejection proves every acceptable move is
    already in the list. When all `multipv` candidates pass, the list may be
    truncated and the client falls back to its own engine for unlisted moves.
    """
    board = chess.Board(fen)
    pov = board.turn
    cutoff = win_before - tolerance

    scored = []
    saw_rejected = False
    for cand in eng.evaluate_multi(engine, board, nodes, multipv=multipv):
        if not cand.best_uci:
            continue
        move = chess.Move.from_uci(cand.best_uci)
        if move not in board.legal_moves:
            continue
        after = board.copy()
        after.push(move)
        # Identical to web.attempt: judge the position after the move, not the
        # multipv line's own score, so the two can never disagree.
        ev = eng.evaluate(engine, after, nodes)
        if _pov_win(ev, pov) >= cutoff:
            scored.append((cand.best_uci, ev.score_cp, ev.score_mate,
                           _pov_win(ev, pov)))
        else:
            saw_rejected = True

    scored.sort(key=lambda s: -s[3])
    return [[uci, cp, mate] for uci, cp, mate, _ in scored], saw_rejected


def ensure(conn, config: dict | None = None, limit: int | None = None,
           progress=None) -> dict:
    """Fill accept_sets for every non-discarded puzzle that lacks a fresh row.

    Commits per puzzle, so an interrupted run resumes where it stopped.
    """
    config = config or load_config()
    nodes = config["trainer"]["check_nodes"]
    tolerance = config["trainer"]["tolerance_winpct"]

    rows = conn.execute(
        """SELECT m.game_id, m.ply, m.fen, m.win_before
           FROM mistakes m
           LEFT JOIN accept_sets a
             ON a.game_id = m.game_id AND a.ply = m.ply
            AND a.fen = m.fen AND a.nodes = ? AND a.multipv = ?
           WHERE m.discarded_at IS NULL AND a.game_id IS NULL
           ORDER BY m.game_id, m.ply""",
        (nodes, MULTIPV),
    ).fetchall()
    if limit:
        rows = rows[:limit]

    done = 0
    if rows:
        with eng.open_engine(config) as engine:
            for row in rows:
                moves, complete = compute_one(
                    engine, row["fen"], row["win_before"], nodes, tolerance)
                conn.execute(
                    """INSERT INTO accept_sets
                           (game_id, ply, fen, nodes, multipv, moves_json,
                            complete, computed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(game_id, ply) DO UPDATE SET
                           fen = excluded.fen, nodes = excluded.nodes,
                           multipv = excluded.multipv,
                           moves_json = excluded.moves_json,
                           complete = excluded.complete,
                           computed_at = excluded.computed_at""",
                    (row["game_id"], row["ply"], row["fen"], nodes, MULTIPV,
                     json.dumps(moves, separators=(",", ":")), int(complete),
                     db.now_s()),
                )
                conn.commit()
                done += 1
                if progress:
                    progress(done, len(rows), row)

    total = conn.execute(
        """SELECT COUNT(*) AS n FROM mistakes m
           JOIN accept_sets a ON a.game_id = m.game_id AND a.ply = m.ply
                             AND a.fen = m.fen AND a.nodes = ? AND a.multipv = ?
           WHERE m.discarded_at IS NULL""", (nodes, MULTIPV)).fetchone()["n"]
    return {"computed": done, "cached": total}


def load_all(conn, config: dict | None = None) -> dict[tuple[str, int], tuple[list, int]]:
    """Every fresh accept set, keyed by (game_id, ply), for the static export."""
    config = config or load_config()
    rows = conn.execute(
        "SELECT game_id, ply, moves_json, complete FROM accept_sets "
        "WHERE nodes = ? AND multipv = ?",
        (config["trainer"]["check_nodes"], MULTIPV),
    ).fetchall()
    return {(r["game_id"], r["ply"]): (json.loads(r["moves_json"]), r["complete"])
            for r in rows}
