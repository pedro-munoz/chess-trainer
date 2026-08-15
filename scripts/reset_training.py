"""Wipe training history (attempts + spaced-repetition state) for a fresh start.

Puzzles, games, evals and explanations are untouched — only the record of what
you have practised. Every puzzle becomes "never seen" and due immediately.

    python -m scripts.reset_training --yes
"""

import argparse

from chess_trainer import db, srs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually delete (required)")
    args = ap.parse_args()

    conn = db.connect()
    n_att = conn.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"]
    n_sch = conn.execute("SELECT COUNT(*) n FROM scheduling").fetchone()["n"]
    print(f"attempts: {n_att} row(s), scheduling: {n_sch} row(s)")
    if not args.yes:
        print("Dry run — pass --yes to delete.")
        return

    srs.reset_training(conn)
    conn.commit()

    left_att = conn.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"]
    left_sch = conn.execute("SELECT COUNT(*) n FROM scheduling").fetchone()["n"]
    puzzles = conn.execute(
        "SELECT COUNT(*) n FROM mistakes WHERE discarded_at IS NULL").fetchone()["n"]
    print(f"Deleted. attempts={left_att}, scheduling={left_sch}; "
          f"{puzzles} puzzles all due as new.")


if __name__ == "__main__":
    main()
