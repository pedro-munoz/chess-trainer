/* Export/import of local training state (static build only).

   The phone holds the only copy of the schedule. navigator.storage.persist()
   protects it from eviction under storage pressure, but nothing protects it
   from "Clear browsing data" — so this exists, and the dashboard nags when the
   last backup gets old.

   The same file feeds scripts/import_phone_state.py, which carries discards
   back to the laptop so re-analysis doesn't resurrect them. */

import * as store from './store.js';

export const BACKUP_KIND = 'chess-trainer-backup';
export const BACKUP_VERSION = 1;
const NAG_AFTER_S = 14 * 86400;

export async function buildBackup() {
  const [srs, discards, attempts, buildId] = await Promise.all([
    store.getAll('srs'), store.getAll('discards'), store.getAll('attempts'),
    store.getMeta('build_id', null),
  ]);
  return {
    kind: BACKUP_KIND,
    version: BACKUP_VERSION,
    exported_at: Math.floor(Date.now() / 1000),
    build_id: buildId,
    srs,
    discards,
    attempts,
  };
}

export async function restoreBackup(payload) {
  if (payload?.kind !== BACKUP_KIND) throw new Error('not a trainer backup file');
  if (payload.version > BACKUP_VERSION) throw new Error('backup is from a newer version');

  // Merge rather than replace: a restore should never lose reviews done since
  // the file was written. Newest last_at wins.
  const existing = await store.loadSrs();
  const merged = [];
  for (const row of payload.srs || []) {
    const mine = existing.get(row.pid);
    if (!mine || (row.last_at || 0) > (mine.last_at || 0)) merged.push(row);
  }
  await store.putMany('srs', merged);
  await store.putMany('discards', payload.discards || []);

  // Attempts are an append-only log with autoIncrement keys; drop the incoming
  // ids so a restore cannot overwrite unrelated local rows.
  const attempts = (payload.attempts || []).map(({ id, ...rest }) => rest);
  await store.putMany('attempts', attempts);

  return { srs: merged.length, discards: (payload.discards || []).length,
           attempts: attempts.length };
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
