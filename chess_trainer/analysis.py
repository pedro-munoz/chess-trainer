"""Analyze synced games with Stockfish and extract mistakes as puzzles."""

import sqlite3
import time

import chess

from . import db, engine as eng, explain, judgments


def _san_line(board: chess.Board, pv_uci: list[str], max_plies: int) -> str:
    """Convert a UCI PV into a SAN line, stopping at the first illegal move."""
    b = board.copy()
    parts = []
    for uci in pv_uci[:max_plies]:
        m = chess.Move.from_uci(uci)
        if m not in b.legal_moves:
            break
        parts.append(b.san(m))
        b.push(m)
    return " ".join(parts)


def pending_games(conn: sqlite3.Connection, order: str, limit: int | None) -> list[sqlite3.Row]:
    direction = "DESC" if order == "newest" else "ASC"
    sql = (
        "SELECT * FROM games WHERE analyzed_at IS NULL AND variant = 'standard' "
        f"AND moves != '' ORDER BY played_at {direction}"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def analyze_game(
    conn: sqlite3.Connection,
    engine,
    game: sqlite3.Row,
    nodes: int,
    thresholds: dict,
) -> int:
    """Analyze one game; returns the number of mistakes found. Commits at the end."""
    board = chess.Board()
    san_moves = game["moves"].split()
    moves: list[chess.Move] = []
    boards: list[chess.Board] = [board.copy()]
    for san in san_moves:
        move = board.parse_san(san)
        moves.append(move)
        board.push(move)
        boards.append(board.copy())

    evals: list[eng.Evaluation] = []
    for b in boards:
        evals.append(eng.evaluate(engine, b, nodes))

    conn.execute("DELETE FROM evals WHERE game_id = ?", (game["id"],))
    # On re-analysis, drop stale non-discarded mistakes (and their training state);
    # discarded ones stay so they never come back.
    stale = [r["id"] for r in conn.execute(
        "SELECT id FROM mistakes WHERE game_id = ? AND discarded_at IS NULL", (game["id"],))]
    if stale:
        marks = ",".join("?" * len(stale))
        conn.execute(f"DELETE FROM attempts WHERE mistake_id IN ({marks})", stale)
        conn.execute(f"DELETE FROM scheduling WHERE mistake_id IN ({marks})", stale)
        conn.execute(f"DELETE FROM mistakes WHERE id IN ({marks})", stale)
    for ply, (b, ev) in enumerate(zip(boards, evals)):
        conn.execute(
            "INSERT INTO evals (game_id, ply, fen, score_cp, score_mate, best_uci, pv) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (game["id"], ply, b.fen(), ev.score_cp, ev.score_mate,
             ev.best_uci, " ".join(ev.pv_uci)),
        )

    user_color = chess.WHITE if game["color"] == "white" else chess.BLACK
    found = 0
    for ply, move in enumerate(moves):
        pos = boards[ply]
        if pos.turn != user_color:
            continue
        ev_before, ev_after = evals[ply], evals[ply + 1]
        cp_before = judgments.score_to_cp(ev_before.score_cp, ev_before.score_mate)
        cp_after = judgments.score_to_cp(ev_after.score_cp, ev_after.score_mate)
        win_before = judgments.win_pct(cp_before, user_color)
        win_after = judgments.win_pct(cp_after, user_color)
        judgment = judgments.judge(win_before, win_after, thresholds)
        if judgment is None:
            continue
        if not ev_before.best_uci or ev_before.best_uci == move.uci():
            continue  # engine agrees with the played move; drop artifact

        best = chess.Move.from_uci(ev_before.best_uci)
        pv_san = _san_line(pos, ev_before.pv_uci, 16)
        punish_san = _san_line(boards[ply + 1], ev_after.pv_uci, 6)

        best_mate = None
        if ev_before.score_mate is not None:
            m = ev_before.score_mate if user_color == chess.WHITE else -ev_before.score_mate
            best_mate = m if m > 0 else None
        played_mate_against = None
        if ev_after.score_mate is not None:
            m = ev_after.score_mate if user_color == chess.WHITE else -ev_after.score_mate
            played_mate_against = -m if m < 0 else None

        idea, played_text = explain.explain_parts(
            pos, move, best, ev_before.pv_uci, best_mate, played_mate_against,
            eval_before=judgments.fmt_eval(ev_before.score_cp, ev_before.score_mate, user_color),
            eval_after=judgments.fmt_eval(ev_after.score_cp, ev_after.score_mate, user_color),
            punish_san=punish_san,
        )
        motif = explain.classify_motif(
            pos, move, best, ev_before.pv_uci, best_mate, played_mate_against,
            punish_uci=ev_after.pv_uci)
        conn.execute(
            """INSERT INTO mistakes (game_id, ply, fen, color, played_san, played_uci,
                                     best_san, best_uci, pv_san, win_before, win_after,
                                     win_loss, judgment, phase, explanation,
                                     explanation_played, explanation_source, motif, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(game_id, ply) DO NOTHING""",
            (game["id"], ply, pos.fen(), game["color"], san_moves[ply], move.uci(),
             pos.san(best), best.uci(), pv_san, round(win_before, 2), round(win_after, 2),
             round(win_before - win_after, 2), judgment,
             judgments.phase_of(pos, ply, game["division_middle"], game["division_end"]),
             idea, played_text, "rules", motif, db.now_s()),
        )
        found += 1

    conn.execute("UPDATE games SET analyzed_at = ? WHERE id = ?", (db.now_s(), game["id"]))
    conn.commit()
    return found


def run(conn: sqlite3.Connection, config: dict, limit: int | None = None,
        nodes: int | None = None, order: str | None = None) -> None:
    nodes = nodes or config["analysis"]["nodes"]
    order = order or config["analysis"]["order"]
    thresholds = config["thresholds"]
    games = pending_games(conn, order, limit)
    if not games:
        print("No games pending analysis.")
        return
    print(f"Analyzing {len(games)} game(s) at {nodes:,} nodes/position...")
    with eng.open_engine(config) as engine:
        for i, game in enumerate(games, 1):
            t0 = time.time()
            found = analyze_game(conn, engine, game, nodes, thresholds)
            print(f"[{i}/{len(games)}] {game['id']} vs {game['opponent']} "
                  f"({len(game['moves'].split())} plies) -> {found} mistake(s) "
                  f"in {time.time() - t0:.1f}s")
