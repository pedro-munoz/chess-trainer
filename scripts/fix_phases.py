"""Recompute mistakes.phase from the stored FEN (one-off, safe to re-run)."""

import chess

from chess_trainer import db, judgments


def main() -> None:
    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")
    rows = conn.execute(
        """SELECT m.id, m.fen, m.ply, m.phase, g.division_middle, g.division_end
           FROM mistakes m JOIN games g ON g.id = m.game_id"""
    ).fetchall()
    changed = 0
    for r in rows:
        phase = judgments.phase_of(
            chess.Board(r["fen"]), r["ply"], r["division_middle"], r["division_end"])
        if phase != r["phase"]:
            conn.execute("UPDATE mistakes SET phase = ? WHERE id = ?", (phase, r["id"]))
            changed += 1
    conn.commit()
    counts = conn.execute(
        "SELECT phase, COUNT(*) AS n FROM mistakes WHERE discarded_at IS NULL "
        "GROUP BY phase ORDER BY phase").fetchall()
    print(f"Reclassified {changed} of {len(rows)} puzzles.")
    for c in counts:
        print(f"  {c['phase']}: {c['n']}")


if __name__ == "__main__":
    main()
