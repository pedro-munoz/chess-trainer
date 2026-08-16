"""Make the laptop a peer in the cross-device training sync.

The phones sync from the browser (web/js/sync.js); this is the same protocol
against the same secret gist, reading and writing SQLite instead of IndexedDB.
Run it with ``python -m chess_trainer sync-state``.

It supersedes scripts/import_phone_state.py, which carried discards one way out
of a manually downloaded backup file. Discards still matter most — a puzzle
discarded on the phone must reach ``mistakes.discarded_at`` or the next export
ships it again — but now the schedule and the attempt log come with them, which
is what finally makes the local ``attempts`` and ``scheduling`` tables real.

## The merge, which must match web/js/merge.js exactly

    scheduling  last-write-wins on last_at; lapses is max(), never the winner's
    discards    union; a discard is never undone by a peer
    attempts    append-only, deduplicated on sync_id

`lapses` counts how often Pedro got a puzzle wrong. It is evidence about him
rather than a property of the schedule, so it survives losing the LWW race —
otherwise a failure recorded on the phone would vanish when the laptop wrote
later.

`h` is carried, never recomputed here: see the note in db._migrate.
"""

import hashlib
import json

from . import db, gist

FILE_PREFIX = "state-"


def puzzle_hash(fen: str, best_uci: str, judgment: str) -> str:
    """Content hash: changes exactly when a puzzle's answer changes.

    The training client resets a card's schedule when this moves, so it must
    cover what makes the puzzle a different question and nothing else.
    Explanation edits deliberately do not count.

    Lives here rather than in scripts/export_static.py because two things now
    depend on it agreeing: the exported puzzle records and the synced schedule.
    """
    return hashlib.sha1(f"{fen}|{best_uci}|{judgment}".encode()).hexdigest()[:8]


def _pid(game_id: str, ply: int) -> str:
    return f"{game_id}:{ply}"


def _resolve(conn, pid: str) -> int | None:
    """pid -> mistakes.id, or None if this database has no such puzzle."""
    game_id, _, ply = pid.rpartition(":")
    if not ply.isdigit():
        return None
    row = conn.execute(
        "SELECT id FROM mistakes WHERE game_id = ? AND ply = ?", (game_id, int(ply))
    ).fetchone()
    return row["id"] if row else None


# ---------- what this device publishes ----------


def backfill_sync_ids(conn) -> int:
    """Give pre-sync attempts an identity. They were all recorded here."""
    rows = conn.execute("SELECT id FROM attempts WHERE sync_id IS NULL").fetchall()
    for row in rows:
        conn.execute("UPDATE attempts SET sync_id = ? WHERE id = ?",
                     (db.new_sync_id(conn), row["id"]))
    return len(rows)


def build_payload(conn, device: str) -> dict:
    scheduling = [
        {"pid": _pid(r["game_id"], r["ply"]), "ease": r["ease"],
         "interval_days": r["interval_days"], "due_at": r["due_at"],
         "reps": r["reps"], "lapses": r["lapses"], "last_at": r["last_at"] or 0,
         **({"h": r["h"]} if r["h"] else {})}
        for r in conn.execute(
            """SELECT m.game_id, m.ply, s.ease, s.interval_days, s.due_at,
                      s.reps, s.lapses, s.last_at, s.h
               FROM scheduling s JOIN mistakes m ON m.id = s.mistake_id""")
    ]
    discards = [
        {"pid": _pid(r["game_id"], r["ply"]), "at": r["discarded_at"]}
        for r in conn.execute(
            "SELECT game_id, ply, discarded_at FROM mistakes "
            "WHERE discarded_at IS NOT NULL")
    ]
    # Only our own attempts: the log is the one store that grows without bound,
    # so it is partitioned by device rather than mirrored by everyone.
    attempts = [
        {"id": r["sync_id"], "pid": _pid(r["game_id"], r["ply"]),
         "at": r["attempted_at"], "uci": r["move_uci"],
         "correct": r["correct"], "took_ms": r["took_ms"]}
        for r in conn.execute(
            """SELECT a.sync_id, a.attempted_at, a.move_uci, a.correct, a.took_ms,
                      m.game_id, m.ply
               FROM attempts a JOIN mistakes m ON m.id = a.mistake_id
               WHERE a.sync_id LIKE ?""", (f"{device}:%",))
    ]
    return {
        "kind": gist.KIND,
        "version": gist.VERSION,
        "device": device,
        "label": db.get_meta(conn, "device_label") or "laptop",
        "build_id": db.get_meta(conn, "build_id"),
        "reset_at": int(db.get_meta(conn, "reset_at") or 0),
        "srs": scheduling,
        "discards": discards,
        "attempts": attempts,
    }


# ---------- what this device takes in ----------


def apply_reset(conn, reset_at: int) -> dict:
    """Honour a "reset training" performed on another device.

    Rows with no last_at predate the column entirely, so they predate the reset.
    """
    sched = conn.execute(
        "DELETE FROM scheduling WHERE COALESCE(last_at, 0) <= ?", (reset_at,)).rowcount
    attempts = conn.execute(
        "DELETE FROM attempts WHERE attempted_at <= ?", (reset_at,)).rowcount
    db.set_meta(conn, "reset_at", str(reset_at))
    return {"scheduling": sched, "attempts": attempts}


SRS_FIELDS = ("ease", "interval_days", "due_at", "reps", "lapses", "last_at", "h")


def merge_srs_row(mine: dict | None, incoming: dict) -> dict | None:
    """One schedule row's merge decision. Returns the row to store, or None.

    The port of web/js/merge.js::mergeSrs, kept as a pure function so the two
    can be tested against each other — they are the halves of one rule, and a
    disagreement would show up as a schedule that quietly differs by device.
    """
    if mine is None:
        return {f: incoming.get(f) for f in SRS_FIELDS}

    mine_at = mine.get("last_at") or 0
    incoming_at = incoming.get("last_at") or 0
    if incoming_at != mine_at:
        incoming_wins = incoming_at > mine_at
    else:
        # Same instant on both sides — a redeploy reset both without either
        # being reviewed since. Prefer more progress, so every device breaks
        # the tie identically no matter which merges first.
        incoming_wins = (incoming.get("reps") or 0) > (mine.get("reps") or 0)

    winner = incoming if incoming_wins else mine
    # Overlay rather than replace, mirroring `{...local, ...winner}` on the JS
    # side: a field the winner simply does not carry keeps the value we hold.
    merged = {f: mine.get(f) for f in SRS_FIELDS}
    merged.update({f: winner[f] for f in SRS_FIELDS if f in winner})
    # lapses is evidence about Pedro, not part of the schedule: it never loses.
    merged["lapses"] = max(mine.get("lapses") or 0, incoming.get("lapses") or 0)
    if all(merged[f] == mine.get(f) for f in SRS_FIELDS):
        return None
    return merged


def _merge_srs(conn, rows, reset_at: int, counts: dict) -> None:
    for row in rows:
        if (row.get("last_at") or 0) <= reset_at:
            continue
        mistake_id = _resolve(conn, row.get("pid", ""))
        if mistake_id is None:
            counts["unknown"] += 1
            continue
        existing = conn.execute(
            "SELECT * FROM scheduling WHERE mistake_id = ?", (mistake_id,)).fetchone()
        merged = merge_srs_row(dict(existing) if existing else None, row)
        if merged is None:
            continue

        conn.execute(
            """INSERT INTO scheduling
                   (mistake_id, ease, interval_days, due_at, reps, lapses, last_at, h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(mistake_id) DO UPDATE SET
                 ease = excluded.ease, interval_days = excluded.interval_days,
                 due_at = excluded.due_at, reps = excluded.reps,
                 lapses = excluded.lapses, last_at = excluded.last_at,
                 h = excluded.h""",
            (mistake_id, merged["ease"] if merged["ease"] is not None else 2.5,
             merged["interval_days"] or 0, merged["due_at"] or 0,
             merged["reps"] or 0, merged["lapses"] or 0,
             merged["last_at"] or 0, merged["h"]))
        counts["srs"] += 1


def _merge_discards(conn, rows, counts: dict) -> None:
    for row in rows:
        mistake_id = _resolve(conn, row.get("pid", ""))
        if mistake_id is None:
            counts["unknown"] += 1
            continue
        cur = conn.execute(
            "UPDATE mistakes SET discarded_at = ? WHERE id = ? AND discarded_at IS NULL",
            (row.get("at") or db.now_s(), mistake_id))
        counts["discards"] += cur.rowcount


def _merge_attempts(conn, rows, reset_at: int, counts: dict) -> None:
    for row in rows:
        if (row.get("at") or 0) <= reset_at:
            continue
        mistake_id = _resolve(conn, row.get("pid", ""))
        if mistake_id is None:
            counts["unknown"] += 1
            continue
        # The unique index on sync_id is what makes re-running this a no-op.
        cur = conn.execute(
            """INSERT OR IGNORE INTO attempts
                   (mistake_id, attempted_at, move_uci, correct, took_ms, sync_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mistake_id, row.get("at"), row.get("uci"), int(row.get("correct") or 0),
             row.get("took_ms"), row.get("id")))
        counts["attempts"] += cur.rowcount


# ---------- the run ----------


def run(conn, *, pull: bool = True, push: bool = True, dry_run: bool = False) -> dict:
    gist_id, token = gist.load_auth()
    device = db.device_id(conn)
    backfilled = backfill_sync_ids(conn)

    peers = gist.read_files(gist_id, token)
    counts = {"srs": 0, "discards": 0, "attempts": 0, "unknown": 0,
              "backfilled": backfilled, "peers": len(peers), "reset": None,
              "pushed": False}

    if pull:
        local_reset = int(db.get_meta(conn, "reset_at") or 0)
        peer_reset = max((p.get("reset_at") or 0 for p in peers.values()),
                         default=0)
        if peer_reset > local_reset:
            counts["reset"] = apply_reset(conn, peer_reset)
        reset_at = max(local_reset, peer_reset)

        for payload in peers.values():
            if payload.get("device") == device:
                continue                                    # our own file
            _merge_srs(conn, payload.get("srs") or [], reset_at, counts)
            _merge_discards(conn, payload.get("discards") or [], counts)
            _merge_attempts(conn, payload.get("attempts") or [], reset_at, counts)

    if push:
        filename = f"{FILE_PREFIX}{device}.json"
        payload = build_payload(conn, device)
        previous = dict(peers.get(filename) or {})
        previous.pop("updated_at", None)
        if previous != payload:
            if not dry_run:
                gist.write_file(gist_id, token,
                                filename, {**payload, "updated_at": db.now_s()})
            counts["pushed"] = True

    if dry_run:
        conn.rollback()
    else:
        db.set_meta(conn, "last_sync_at", str(db.now_s()))
        conn.commit()
    return counts


def report(counts: dict) -> str:
    lines = [f"Read {counts['peers']} device file(s) from the gist."]
    if counts["backfilled"]:
        lines.append(f"  backfilled: {counts['backfilled']} attempt(s) given a sync id")
    if counts["reset"]:
        lines.append(f"  reset:      honoured a training reset from another device "
                     f"({counts['reset']['scheduling']} schedule row(s), "
                     f"{counts['reset']['attempts']} attempt(s) dropped)")
    lines += [
        f"  pulled:     {counts['srs']} schedule row(s), "
        f"{counts['attempts']} attempt(s), {counts['discards']} discard(s)",
        f"  pushed:     {'yes' if counts['pushed'] else 'nothing changed'}",
    ]
    if counts["unknown"]:
        lines.append(f"  unknown:    {counts['unknown']} pid(s) not in this database")
    if counts["discards"]:
        lines.append("\nDiscards changed — re-export so they stop shipping:\n"
                     "  python -m scripts.deploy_pages")
    return "\n".join(lines)


def write_auth_template(path=None) -> str:
    """Create data/gist_auth.json with blanks, so the format is never guessed."""
    path = path or gist.AUTH_PATH
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        return f"{path} already exists — left alone."
    path.write_text(json.dumps({"gist_id": "", "token": ""}, indent=2) + "\n",
                    encoding="utf-8")
    return (f"Wrote {path}. Fill in the secret gist's id and a classic GitHub "
            "token with the gist scope.")
