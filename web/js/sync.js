/* Cross-device sync through a secret GitHub Gist (static build only).

   Training state lives in each device's IndexedDB. This mirrors it through one
   secret gist so the phone, a tablet and the laptop share a single schedule.

   ## Why a gist, and why one file per device

   The gist API has no conditional write — no ETag, no If-Match — so the obvious
   design (one blob, read-modify-write) can lose an update whenever two devices
   are open at once. Instead every device owns a file, `state-<device>.json`,
   and writes only that one. Nobody ever overwrites anybody, so the conflict
   window is not narrowed but removed. Reading is the union of all the files,
   folded together by merge.js.

   What each file carries follows from how its store merges:

     srs       this device's whole view. Redundant across files, but it makes
               every file a complete restore point and costs ~110 bytes a row
     discards  same, and a union never needs an owner
     attempts  only the ones this device recorded, found by the `<device>:`
               prefix on their ids. The log is the only store that grows without
               bound, so it is the one that must not be copied three times

   ## What this is not

   Not a general sync engine. It assumes one person, a handful of devices, and
   long gaps between writes — Pedro trains on one device at a time. Two devices
   answering the same puzzle in the same second resolve by merge.js's tie-break
   rather than anything cleverer.

   The token is a classic PAT with the `gist` scope, held in localStorage.
   Fine-grained tokens still cannot reach gists, and `gist` cannot be narrowed
   to a single gist, so that token can read and write every gist on the account.
   It is deliberately not in config.js: it is per-device, and never built. */

import * as store from './store.js';
import * as merge from './merge.js';
import { refresh } from './api.local.js';

const API = 'https://api.github.com';
const KIND = 'chess-trainer-sync';
const VERSION = 1;
const FILE_RE = /^state-[\w-]+\.json$/;
const DEBOUNCE_MS = 8000;

const LS_TOKEN = 'ct-sync-token';
const LS_GIST = 'ct-sync-gist';

/* ---------- configuration (device-local, never exported) ---------- */

export const getConfig = () => ({
  token: localStorage.getItem(LS_TOKEN) || '',
  gistId: localStorage.getItem(LS_GIST) || '',
});

export function setConfig({ token, gistId }) {
  // Accept a pasted gist URL as well as a bare id.
  const id = String(gistId || '').trim().replace(/\/$/, '').split('/').pop();
  localStorage.setItem(LS_TOKEN, String(token || '').trim());
  localStorage.setItem(LS_GIST, id || '');
}

export function clearConfig() {
  localStorage.removeItem(LS_TOKEN);
  localStorage.removeItem(LS_GIST);
}

export function isConfigured() {
  const { token, gistId } = getConfig();
  return Boolean(token && gistId);
}

/* ---------- transport ---------- */

class SyncError extends Error {}

async function api(path, options = {}) {
  const { token } = getConfig();
  let resp;
  try {
    resp = await fetch(API + path, {
      ...options,
      headers: {
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        Authorization: `Bearer ${token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch {
    throw new SyncError('offline — no connection to GitHub');
  }
  if (resp.ok) return resp.json();

  if (resp.status === 401) throw new SyncError('token rejected — check it has not expired');
  if (resp.status === 404) {
    throw new SyncError('gist not found — check the id, and that the token is a '
                      + 'classic token with the gist scope');
  }
  if (resp.status === 403 || resp.status === 429) {
    throw new SyncError('GitHub rate limit hit — try again in a few minutes');
  }
  throw new SyncError(`GitHub returned ${resp.status}`);
}

/* Gist files over ~1 MB come back flagged `truncated` with the content elided,
   and have to be re-fetched from raw_url. Our files sit well under that, but
   the attempt log is the one thing that grows forever, so handle it rather than
   silently dropping a device's history. */
async function fileContent(file) {
  if (!file.truncated && file.content) return file.content;
  try {
    const resp = await fetch(file.raw_url);
    if (!resp.ok) throw new Error();
    return resp.text();
  } catch {
    throw new SyncError(`${file.filename} is too large to read back — `
                      + 'its attempt log needs trimming');
  }
}

async function readPeers() {
  const gist = await api(`/gists/${getConfig().gistId}`);
  const out = [];
  for (const file of Object.values(gist.files || {})) {
    if (!FILE_RE.test(file.filename)) continue;
    let payload;
    try {
      payload = JSON.parse(await fileContent(file));
    } catch (err) {
      if (err instanceof SyncError) throw err;
      throw new SyncError(`${file.filename} is not valid JSON`);
    }
    if (payload?.kind !== KIND) continue;
    if (payload.version > VERSION) {
      throw new SyncError(`${file.filename} was written by a newer build`);
    }
    out.push({ filename: file.filename, payload });
  }
  return out;
}

/* ---------- reset propagation ----------

   "Reset training" wipes the schedule locally. Without a marker the next pull
   would restore it from a peer that had not heard, so the reset travels as a
   timestamp: every device drops the rows older than the newest stamp it sees,
   and merge.js refuses to import older ones. Discards survive, matching
   srs.reset_training on the Python side. */
async function applyReset(resetAt) {
  const [srs, attempts] = await Promise.all([
    store.getAll('srs'), store.getAll('attempts'),
  ]);
  await store.delMany('srs',
    srs.filter((r) => (r.last_at || 0) <= resetAt).map((r) => r.pid));
  await store.delMany('attempts',
    attempts.filter((a) => (a.at || 0) <= resetAt).map((a) => a.id));
  await store.setMeta('reset_at', resetAt);
}

/* ---------- the sync itself ---------- */

async function pull(peers, device) {
  const localReset = await store.getMeta('reset_at', 0);
  const resetAt = peers.reduce((max, p) => Math.max(max, p.payload.reset_at || 0),
                               localReset);
  if (resetAt > localReset) await applyReset(resetAt);

  const [srsMap, discardSet, attempts] = await Promise.all([
    store.loadSrs(), store.loadDiscards(), store.getAll('attempts'),
  ]);
  const knownIds = new Set(attempts.map((a) => a.id));
  const counts = { srs: 0, discards: 0, attempts: 0 };

  for (const { payload } of peers) {
    if (payload.device === device) continue;          // our own file
    const srsWrites = merge.mergeSrs(srsMap, payload.srs, resetAt);
    const discardWrites = merge.mergeDiscards(discardSet, payload.discards);
    const attemptWrites = merge.mergeAttempts(knownIds, payload.attempts, resetAt);
    await store.putMany('srs', srsWrites);
    await store.putMany('discards', discardWrites);
    await store.putMany('attempts', attemptWrites);
    counts.srs += srsWrites.length;
    counts.discards += discardWrites.length;
    counts.attempts += attemptWrites.length;
  }
  return counts;
}

async function buildPayload(device) {
  const [srs, discards, attempts, label, resetAt, buildId] = await Promise.all([
    store.getAll('srs'), store.getAll('discards'), store.getAll('attempts'),
    store.deviceLabel(), store.getMeta('reset_at', 0),
    store.getMeta('build_id', null),
  ]);
  const prefix = `${device}:`;
  return {
    kind: KIND,
    version: VERSION,
    device,
    label,
    build_id: buildId,
    reset_at: resetAt,
    // orphan_since is this device's opinion of its own dataset; never shared.
    srs: srs.map(({ orphan_since: _o, ...rest }) => rest),
    discards,
    attempts: attempts.filter((a) => String(a.id).startsWith(prefix)),
  };
}

async function push(peers, device) {
  const filename = `state-${device}.json`;
  const payload = await buildPayload(device);
  const body = JSON.stringify(payload, null, 1);

  // Skip the write when nothing changed — `updated_at` is stamped only on a
  // real push so an idle device does not churn the gist's history.
  const mine = peers.find((p) => p.filename === filename);
  if (mine) {
    const { updated_at: _drop, ...previous } = mine.payload;
    if (JSON.stringify(previous, null, 1) === body) return false;
  }

  const content = JSON.stringify(
    { ...payload, updated_at: Math.floor(Date.now() / 1000) }, null, 1);
  await api(`/gists/${getConfig().gistId}`, {
    method: 'PATCH',
    body: JSON.stringify({ files: { [filename]: { content } } }),
  });
  return true;
}

let inFlight = null;

/** Pull every peer's file, merge it in, then publish ours.

    Concurrent callers share one run: the trainer syncs after attempts while
    the dashboard syncs on load, and two overlapping passes would merge the
    same rows twice. */
export function sync() {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    if (!isConfigured()) throw new SyncError('sync is not set up on this device');
    await store.migrateAttemptIds();
    const device = await store.deviceId();
    const peers = await readPeers();
    const pulled = await pull(peers, device);
    if (pulled.srs || pulled.discards || pulled.attempts) await refresh();
    const pushed = await push(peers, device);
    const now = Math.floor(Date.now() / 1000);
    await store.setMeta('last_sync_at', now);
    return { ...pulled, pushed, at: now, peers: peers.length };
  })().finally(() => { inFlight = null; });
  return inFlight;
}

/** Fire-and-forget sync that never rejects — for automatic triggers. */
export async function syncQuiet() {
  try {
    return await sync();
  } catch {
    return null;                                 // offline is the normal case
  }
}

let timer = null;

/** Coalesce the bursts of activity a training session produces into one push. */
export function syncSoon() {
  if (!isConfigured()) return;
  clearTimeout(timer);
  timer = setTimeout(syncQuiet, DEBOUNCE_MS);
}

/** Flush a pending sync while the page can still make requests. */
export function syncNow() {
  if (!timer) return;
  clearTimeout(timer);
  timer = null;
  syncQuiet();
}

export const lastSyncAt = () => store.getMeta('last_sync_at', 0);

/** Install the automatic triggers. Safe to call when sync is not configured. */
export function autoSync() {
  syncQuiet();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') syncNow();
    else syncQuiet();
  });
}

/* ---------- dashboard controls ---------- */

const el = (id) => document.getElementById(id);

function ago(seconds) {
  if (!seconds) return 'never';
  const d = Math.floor(Date.now() / 1000) - seconds;
  if (d < 90) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 172800) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} days ago`;
}

export async function mountSyncUi(reload) {
  const host = el('sync-zone');
  if (!host) return;
  host.hidden = false;

  const note = el('sync-note');
  const form = el('sync-form');

  async function describe(extra) {
    if (extra) { note.textContent = extra; return; }
    if (!isConfigured()) {
      note.textContent = 'Not set up. Training progress stays on this device only.';
      host.classList.add('off');
      return;
    }
    host.classList.remove('off');
    const label = await store.deviceLabel();
    note.textContent = `This device syncs as “${label}”. `
                     + `Last synced ${ago(await lastSyncAt())}.`;
  }
  await describe();

  el('sync-setup').addEventListener('click', async () => {
    form.hidden = !form.hidden;
    if (form.hidden) return;
    const cfg = getConfig();
    el('sync-gist').value = cfg.gistId;
    el('sync-token').value = cfg.token;
    el('sync-label').value = await store.deviceLabel();
  });

  el('sync-now').addEventListener('click', async () => {
    if (!isConfigured()) { await describe('Set up sync first.'); return; }
    el('sync-now').disabled = true;
    await describe('Syncing…');
    try {
      const r = await sync();
      const pulled = r.srs + r.discards + r.attempts;
      await describe(
        `Synced with ${r.peers} device file(s). `
        + (pulled ? `Pulled ${r.srs} schedule row(s), ${r.attempts} attempt(s), `
                  + `${r.discards} discard(s).`
                  : 'Already up to date.')
        + (r.pushed ? ' This device\'s state published.' : ''));
      reload?.();
    } catch (err) {
      await describe(`Sync failed: ${err.message}`);
    }
    el('sync-now').disabled = false;
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    setConfig({ token: el('sync-token').value, gistId: el('sync-gist').value });
    const label = el('sync-label').value.trim();
    if (label) await store.setDeviceLabel(label);
    form.hidden = true;
    await describe();
    el('sync-now').click();
  });

  el('sync-forget').addEventListener('click', async () => {
    clearConfig();
    form.hidden = true;
    await describe('Sync switched off here. Training state on this device is untouched.');
  });
}
