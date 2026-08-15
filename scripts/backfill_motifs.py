"""Backfill mistakes.motif from stored eval data (no re-analysis needed)."""

from collections import Counter

import chess

from chess_trainer import db, explain


def main() -> None:
    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")
    rows = conn.execute(
        """SELECT m.id, m.game_id, m.ply, m.fen, m.played_uci, m.best_uci
           FROM mistakes m"""
    ).fetchall()
    counts: Counter[str] = Counter()
    updated = 0
    for r in rows:
        ev_before = conn.execute(
            "SELECT pv, score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
            (r["game_id"], r["ply"])).fetchone()
        ev_after = conn.execute(
            "SELECT pv, score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
            (r["game_id"], r["ply"] + 1)).fetchone()
        if not ev_before or not ev_after:
            continue  # motif stays NULL; treated as positional at read time

        board = chess.Board(r["fen"])
        user_color = board.turn
        played = chess.Move.from_uci(r["played_uci"])
        best = chess.Move.from_uci(r["best_uci"])
        pv_uci = (ev_before["pv"] or "").split()
        punish_uci = (ev_after["pv"] or "").split()

        best_mate = None
        if ev_before["score_mate"] is not None:
            m = ev_before["score_mate"] if user_color == chess.WHITE else -ev_before["score_mate"]
            best_mate = m if m > 0 else None
        played_mate_against = None
        if ev_after["score_mate"] is not None:
            m = ev_after["score_mate"] if user_color == chess.WHITE else -ev_after["score_mate"]
            played_mate_against = -m if m < 0 else None

        motif = explain.classify_motif(
            board, played, best, pv_uci, best_mate, played_mate_against,
            punish_uci=punish_uci)
        conn.execute("UPDATE mistakes SET motif = ? WHERE id = ?", (motif, r["id"]))
        counts[motif] += 1
        updated += 1
    conn.commit()
    print(f"Classified motifs for {updated} of {len(rows)} puzzles.")
    for motif in explain.MOTIFS:
        print(f"  {motif:16s} {counts.get(motif, 0)}")


if __name__ == "__main__":
    main()
