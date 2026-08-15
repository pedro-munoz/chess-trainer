"""Merge a phone backup back into the laptop DB.

    python -m scripts.import_phone_state --file chess-trainer-backup-2026-08-15.json

Training happens on the phone, so the laptop does not need the schedule. What it
*does* need is the discards: a puzzle discarded on the phone is only hidden
locally, and the next export would ship it again. Writing discarded_at here both
drops it from the next build and stops re-analysis resurrecting it.

--srs also mirrors the schedule and attempt log into the local tables, which is
only useful if you want to look at the history with SQL. It is off by default.
"""

import argparse
import json

from chess_trainer import db


def resolve(conn, pid: str):
    game_id, _, ply = pid.rpartition(":")
    if not ply.isdigit():
        return None
    row = conn.execute(
        "SELECT id FROM mistakes WHERE game_id = ? AND ply = ?", (game_id, int(ply))
    ).fetchone()
    return row["id"] if row else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", required=True, help="backup JSON downloaded from the trainer")
    ap.add_argument("--srs", action="store_true",
                    help="also import the schedule and attempt log")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        payload = json.load(fh)
    if payload.get("kind") != "chess-trainer-backup":
        raise SystemExit(f"{args.file} is not a trainer backup")

    conn = db.connect()
    conn.execute("PRAGMA busy_timeout = 30000")
    now = db.now_s()
    discarded = unknown = 0

    for row in payload.get("discards", []):
        mid = resolve(conn, row["pid"])
        if mid is None:
            unknown += 1
            continue
        cur = conn.execute(
            "UPDATE mistakes SET discarded_at = ? WHERE id = ? AND discarded_at IS NULL",
            (row.get("at") or now, mid))
        discarded += cur.rowcount

    imported_srs = imported_attempts = 0
    if args.srs:
        for row in payload.get("srs", []):
            mid = resolve(conn, row["pid"])
            if mid is None:
                continue
            conn.execute(
                """INSERT INTO scheduling (mistake_id, ease, interval_days, due_at, reps, lapses)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(mistake_id) DO UPDATE SET
                     ease = excluded.ease, interval_days = excluded.interval_days,
                     due_at = excluded.due_at, reps = excluded.reps,
                     lapses = excluded.lapses""",
                (mid, row["ease"], row["interval_days"], row["due_at"],
                 row["reps"], row["lapses"]))
            imported_srs += 1
        for row in payload.get("attempts", []):
            mid = resolve(conn, row["pid"])
            if mid is None:
                continue
            conn.execute(
                "INSERT INTO attempts (mistake_id, attempted_at, move_uci, correct, took_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (mid, row["at"], row.get("uci"), row["correct"], row.get("took_ms")))
            imported_attempts += 1

    if args.dry_run:
        conn.rollback()
        print("(dry run - nothing written)")
    else:
        conn.commit()

    exported = payload.get("exported_at")
    print(f"Backup from build {payload.get('build_id')}"
          + (f", exported {exported}" if exported else ""))
    print(f"  discarded:  {discarded} puzzles newly marked discarded")
    if unknown:
        print(f"  unknown:    {unknown} pids not in this DB (re-analyzed away?)")
    if args.srs:
        print(f"  scheduling: {imported_srs} rows")
        print(f"  attempts:   {imported_attempts} rows")
    if discarded:
        print("\nRe-run the export so the discarded puzzles stop shipping:")
        print("  python -m scripts.deploy_pages")


if __name__ == "__main__":
    main()
