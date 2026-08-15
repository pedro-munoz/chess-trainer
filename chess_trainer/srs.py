"""SM-2-lite spaced repetition over puzzles (Anki-style).

Failed puzzles come back within minutes; solved ones back off exponentially.
"""

import sqlite3

FAIL_RETRY_S = 10 * 60          # failed puzzle is due again in 10 minutes
FIRST_INTERVALS_DAYS = [1, 3]   # first two successful reviews
MIN_EASE, MAX_EASE = 1.3, 3.2
DAY_S = 86_400


def review(conn: sqlite3.Connection, mistake_id: int, correct: bool, now: int) -> dict:
    row = conn.execute(
        "SELECT * FROM scheduling WHERE mistake_id = ?", (mistake_id,)
    ).fetchone()
    ease = row["ease"] if row else 2.5
    interval = row["interval_days"] if row else 0.0
    reps = row["reps"] if row else 0
    lapses = row["lapses"] if row else 0

    if correct:
        reps += 1
        ease = min(MAX_EASE, ease + 0.05)
        if reps <= len(FIRST_INTERVALS_DAYS):
            interval = FIRST_INTERVALS_DAYS[reps - 1]
        else:
            interval = interval * ease
        due_at = now + int(interval * DAY_S)
    else:
        reps = 0
        lapses += 1
        ease = max(MIN_EASE, ease - 0.2)
        interval = 0.0
        due_at = now + FAIL_RETRY_S

    conn.execute(
        """INSERT INTO scheduling (mistake_id, ease, interval_days, due_at, reps, lapses)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(mistake_id) DO UPDATE SET
             ease = excluded.ease, interval_days = excluded.interval_days,
             due_at = excluded.due_at, reps = excluded.reps, lapses = excluded.lapses""",
        (mistake_id, ease, interval, due_at, reps, lapses),
    )
    return {"ease": ease, "interval_days": interval, "due_at": due_at,
            "reps": reps, "lapses": lapses}


def reset_training(conn: sqlite3.Connection) -> dict:
    """Wipe all practice history: every puzzle becomes new and due now.

    Puzzles, games, evals and explanations are untouched. Like review(), the
    caller commits.
    """
    attempts = conn.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"]
    scheduling = conn.execute("SELECT COUNT(*) n FROM scheduling").fetchone()["n"]
    conn.execute("DELETE FROM attempts")
    conn.execute("DELETE FROM scheduling")
    # Restart attempt ids at 1 so the fresh start is fresh in the log too.
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'attempts'")
    return {"attempts_deleted": attempts, "scheduling_deleted": scheduling}
