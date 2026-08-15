"""Rebuild pv_san and explanations for all mistakes from stored eval data."""

import chess

from chess_trainer import db, explain, judgments
from chess_trainer.analysis import _san_line


def main() -> None:
    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")
    rows = conn.execute(
        """SELECT m.id, m.game_id, m.ply, m.fen, m.played_uci, m.best_uci,
                  m.win_before, m.win_after
           FROM mistakes m
           WHERE m.explanation_source != 'claude'"""  # never clobber Claude's text
    ).fetchall()
    updated = 0
    for r in rows:
        ev_before = conn.execute(
            "SELECT pv, score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
            (r["game_id"], r["ply"])).fetchone()
        ev_after = conn.execute(
            "SELECT pv, score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
            (r["game_id"], r["ply"] + 1)).fetchone()
        if not ev_before or not ev_after:
            continue

        board = chess.Board(r["fen"])
        user_color = board.turn
        played = chess.Move.from_uci(r["played_uci"])
        best = chess.Move.from_uci(r["best_uci"])
        pv_uci = (ev_before["pv"] or "").split()
        board_after = board.copy()
        board_after.push(played)

        pv_san = _san_line(board, pv_uci, 16)
        punish_san = _san_line(board_after, (ev_after["pv"] or "").split(), 6)

        best_mate = None
        if ev_before["score_mate"] is not None:
            m = ev_before["score_mate"] if user_color == chess.WHITE else -ev_before["score_mate"]
            best_mate = m if m > 0 else None
        played_mate_against = None
        if ev_after["score_mate"] is not None:
            m = ev_after["score_mate"] if user_color == chess.WHITE else -ev_after["score_mate"]
            played_mate_against = -m if m < 0 else None

        idea, played_text = explain.explain_parts(
            board, played, best, pv_uci, best_mate, played_mate_against,
            eval_before=judgments.fmt_eval(ev_before["score_cp"], ev_before["score_mate"], user_color),
            eval_after=judgments.fmt_eval(ev_after["score_cp"], ev_after["score_mate"], user_color),
            punish_san=punish_san,
        )
        conn.execute(
            "UPDATE mistakes SET pv_san = ?, explanation = ?, explanation_played = ? "
            "WHERE id = ?",
            (pv_san, idea, played_text, r["id"]))
        updated += 1
    conn.commit()
    print(f"Regenerated explanations for {updated} of {len(rows)} puzzles.")
    sample = conn.execute(
        "SELECT explanation, explanation_played FROM mistakes WHERE judgment = 'blunder' "
        "ORDER BY RANDOM() LIMIT 3").fetchall()
    for s in sample:
        print("- IDEA:", s["explanation"])
        print("  GAME:", s["explanation_played"])


if __name__ == "__main__":
    main()
