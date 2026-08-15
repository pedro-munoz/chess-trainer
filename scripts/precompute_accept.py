"""Precompute acceptable-move sets for every puzzle (see chess_trainer/accept.py).

Run after `analyze` picks up new games; the static export runs it implicitly.
Commits per puzzle, so Ctrl+C is safe and a rerun resumes where it stopped.

    python -m scripts.precompute_accept [--limit N]
"""

import argparse
import time

from chess_trainer import accept, db


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, help="stop after N puzzles")
    args = ap.parse_args()

    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")
    started = time.time()

    def progress(done: int, total: int, row) -> None:
        rate = (time.time() - started) / done
        eta = (total - done) * rate
        print(f"  [{done}/{total}] {row['game_id']}:{row['ply']}  "
              f"{rate:.1f}s/puzzle, ~{eta / 60:.0f} min left", flush=True)

    result = accept.ensure(conn, limit=args.limit, progress=progress)
    print(f"Computed {result['computed']} accept sets "
          f"({result['cached']} puzzles cached in total) "
          f"in {(time.time() - started) / 60:.1f} min.")


if __name__ == "__main__":
    main()
