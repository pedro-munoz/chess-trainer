/* Export/import of local training state (static build only).

   The phone holds the only copy of the schedule. navigator.storage.persist()
   protects it from eviction under storage pressure, but nothing protects it
   from "Clear browsing data" — so this exists, and the dashboard nags when the
   last backup gets old.

   The same file feeds scripts/import_phone_state.py, which carries discards
   back to the laptop so re-analysis doesn't resurrect them. */

import * as store from './store.js';
import * as merge from './merge.js';

export const BACKUP_KIND = 'chess-trainer-backup';
export const BACKUP_VERSION = 2;
const NAG_AFTER_S = 14 * 86400;

export async function buildBackup() {
  await store.migrateAttemptIds();
  const [srs, discards, attempts, buildId, device, label, resetAt] = await Promise.all([
    store.getAll('srs'), store.getAll('discards'), store.getAll('attempts'),
    store.getMeta('build_id', null), store.deviceId(), store.deviceLabel(),
    store.getMeta('reset_at', 0),
  ]);
  return {
    kind: BACKUP_KIND,
    version: BACKUP_VERSION,
    exported_at: Math.floor(Date.now() / 1000),
    build_id: buildId,
    device,
    device_label: label,
    reset_at: resetAt,
    srs,
    discards,
    attempts,
  };
}

export async function restoreBackup(payload) {
  if (payload?.kind !== BACKUP_KIND) throw new Error('not a trainer backup file');
  if (payload.version > BACKUP_VERSION) throw new Error('backup is from a newer version');

  // Merge rather than replace: a restore should never lose reviews done since
  // the file was written. Rules live in merge.js, shared with sync.js — in
  // particular the dedupe that stops a second restore doubling the attempt log.
  await store.migrateAttemptIds();
  const [srsMap, discardSet, attempts] = await Promise.all([
    store.loadSrs(), store.loadDiscards(), store.getAll('attempts'),
  ]);
  const knownIds = new Set(attempts.map((a) => a.id));
  const resetAt = await store.getMeta('reset_at', 0);

  const srsWrites = merge.mergeSrs(srsMap, payload.srs, resetAt);
  const discardWrites = merge.mergeDiscards(discardSet, payload.discards);
  const attemptWrites = merge.mergeAttempts(knownIds, payload.attempts, resetAt);

  await store.putMany('srs', srsWrites);
  await store.putMany('discards', discardWrites);
  await store.putMany('attempts', attemptWrites);

  return { srs: srsWrites.length, discards: discardWrites.length,
           attempts: attemptWrites.length };
}

function download(payload) {
  const stamp = new Date().toISOString().slice(0, 10);
  const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `chess-trainer-backup-${stamp}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const fmtBytes = (n) => (n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB`
                                     : `${Math.round(n / 1024)} KB`);

export async function mountBackupUi(reload) {
  const host = document.getElementById('backup-zone');
  if (!host) return;
  host.hidden = false;

  const note = document.getElementById('backup-note');
  const lastAt = await store.getMeta('last_backup_at', 0);
  const estimate = await store.storageEstimate();
  const persisted = await navigator.storage?.persisted?.().catch(() => false);

  const describe = (extra) => {
    const age = lastAt
      ? `Last backup ${Math.floor((Date.now() / 1000 - lastAt) / 86400)} days ago.`
      : 'Never backed up.';
    const stale = !lastAt || (Date.now() / 1000 - lastAt) > NAG_AFTER_S;
    const storage = estimate
      ? ` Using ${fmtBytes(estimate.usage)}${persisted ? ', protected from eviction' : ''}.`
      : '';
    note.textContent = extra || (age + storage);
    host.classList.toggle('stale', stale && !extra);
  };
  describe();

  document.getElementById('backup-export').addEventListener('click', async () => {
    download(await buildBackup());
    const now = Math.floor(Date.now() / 1000);
    await store.setMeta('last_backup_at', now);
    describe('Backup downloaded. Keep it somewhere off this phone.');
  });

  const picker = document.getElementById('backup-file');
  document.getElementById('backup-import').addEventListener('click', () => picker.click());
  picker.addEventListener('change', async () => {
    const file = picker.files?.[0];
    if (!file) return;
    try {
      const r = await restoreBackup(JSON.parse(await file.text()));
      describe(`Restored ${r.srs} schedule rows, ${r.discards} discards, `
             + `${r.attempts} attempts. Reloading…`);
      setTimeout(() => location.reload(), 800);
    } catch (err) {
      describe(`Import failed: ${err.message}`);
    }
    picker.value = '';
    reload?.();
  });
}
