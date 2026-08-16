/* Merge rules for training state arriving from somewhere else.

   Two callers: backup.js restoring a downloaded file, and sync.js folding in
   the other devices' gist files. Both need identical semantics, because both
   run repeatedly over overlapping data — a merge that is not idempotent shows
   up as a slowly inflating attempt count, which is exactly the bug the old
   restore path had.

   The three stores each get the rule their shape allows:

     srs       last-write-wins on `last_at`, except `lapses`, which is max()
     discards  union — a discard is a decision, never undone by a peer
     attempts  append-only, deduplicated by id

   `lapses` is the one field that must not follow the winner. It counts how
   often Pedro got this puzzle wrong, not what the schedule currently looks
   like, and api.local.js weights focus sessions by `15 * lapses` — plain LWW
   would quietly discard a failure recorded on the other device.

   Everything here is pure: callers decide what to write. */

/** `orphan_since` describes one device's dataset, not the schedule. */
const shareable = ({ orphan_since: _drop, ...rest }) => rest;

const at = (r) => r.last_at || 0;

function pick(local, incoming) {
  if (at(incoming) !== at(local)) return at(incoming) > at(local) ? incoming : local;
  // Same timestamp on both sides — happens when a redeploy resets a schedule
  // that neither device has reviewed since. Prefer the row with more progress
  // so the tie breaks the same way whichever device merges first.
  return (incoming.reps || 0) > (local.reps || 0) ? incoming : local;
}

const SRS_FIELDS = ['ease', 'interval_days', 'due_at', 'reps', 'lapses', 'h', 'last_at'];
const sameSrs = (a, b) => SRS_FIELDS.every((f) => (a[f] ?? null) === (b[f] ?? null));

/** Fold incoming srs rows into a Map keyed by pid. Returns the rows to write.

    Mutates `localMap` so the caller's in-memory view matches what it persists.
    Rows for pids this build no longer has are kept: the peer may be on a newer
    dataset, and reconcile() tombstones them anyway. */
export function mergeSrs(localMap, incoming, resetAt = 0) {
  const writes = [];
  for (const raw of incoming || []) {
    if (!raw?.pid) continue;
    if (at(raw) <= resetAt) continue;          // predates a reset we honour
    const row = shareable(raw);
    const local = localMap.get(row.pid);
    if (!local) {
      localMap.set(row.pid, row);
      writes.push(row);
      continue;
    }
    const winner = pick(local, row);
    const merged = {
      ...local, ...winner,
      lapses: Math.max(local.lapses || 0, row.lapses || 0),
    };
    // orphan_since is local state and survives the merge untouched.
    if (local.orphan_since) merged.orphan_since = local.orphan_since;
    if (sameSrs(local, merged)) continue;
    localMap.set(row.pid, merged);
    writes.push(merged);
  }
  return writes;
}

/** Union of discards. Returns only the pids the local set did not have. */
export function mergeDiscards(localSet, incoming) {
  const writes = [];
  for (const row of incoming || []) {
    const pid = row?.pid;
    if (!pid || localSet.has(pid)) continue;
    localSet.add(pid);
    writes.push({ pid, at: row.at || 0 });
  }
  return writes;
}

/* Attempts from a pre-sync backup have no id (restoreBackup used to strip the
   autoIncrement key). Derive a deterministic one from the content so restoring
   the same file twice is a no-op instead of doubling the log. */
const attemptId = (r) => r.id || `legacy:${r.pid}:${r.at}:${r.uci || ''}`;

/** Returns the incoming attempts not already present, ids normalized. */
export function mergeAttempts(knownIds, incoming, resetAt = 0) {
  const writes = [];
  for (const raw of incoming || []) {
    if (!raw?.pid) continue;
    if ((raw.at || 0) <= resetAt) continue;
    const id = attemptId(raw);
    if (knownIds.has(id)) continue;
    knownIds.add(id);
    writes.push({ ...raw, id });
  }
  return writes;
}
