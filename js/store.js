/* Local training state for the static build (IndexedDB).

   In the static build this replaces the server's `attempts` and `scheduling`
   tables. It is no longer the *only* copy — sync.js mirrors it through a secret
   gist so several devices share one schedule — but it is still the copy that
   training reads and writes, and the only one that works offline. Hence
   backup.js, and hence navigator.storage.persist().

   Everything is keyed by pid ("<game_id>:<ply>"), not by mistakes.id, which
   re-analysis rebuilds. Records also carry `h`, the puzzle's content hash: when
   a redeploy changes a puzzle's answer the schedule resets but the lapse count
   and attempt log survive. */

const DB_NAME = 'chess-trainer';
const DB_VERSION = 1;

let dbPromise = null;

function open() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('srs')) {
        db.createObjectStore('srs', { keyPath: 'pid' });
      }
      if (!db.objectStoreNames.contains('attempts')) {
        const s = db.createObjectStore('attempts', { keyPath: 'id', autoIncrement: true });
        s.createIndex('pid', 'pid');
      }
      if (!db.objectStoreNames.contains('discards')) {
        db.createObjectStore('discards', { keyPath: 'pid' });
      }
      if (!db.objectStoreNames.contains('meta')) {
        db.createObjectStore('meta', { keyPath: 'key' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

function tx(store, mode, fn) {
  return open().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const result = fn(t.objectStore(store));
    t.oncomplete = () => resolve(result?.result !== undefined ? result.result : result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  }));
}

export const getAll = (store) => tx(store, 'readonly', (s) => s.getAll());
export const put = (store, value) => tx(store, 'readwrite', (s) => s.put(value));
export const clear = (store) => tx(store, 'readwrite', (s) => s.clear());

export async function putMany(store, values) {
  if (!values.length) return;
  return tx(store, 'readwrite', (s) => values.forEach((v) => s.put(v)));
}

export async function delMany(store, keys) {
  if (!keys.length) return;
  return tx(store, 'readwrite', (s) => keys.forEach((k) => s.delete(k)));
}

export async function getMeta(key, fallback = null) {
  const row = await tx('meta', 'readonly', (s) => s.get(key));
  return row ? row.value : fallback;
}

export const setMeta = (key, value) => put('meta', { key, value });

/* ---------- training state ---------- */

/** All SRS rows as a Map keyed by pid. */
export async function loadSrs() {
  const rows = await getAll('srs');
  return new Map(rows.map((r) => [r.pid, r]));
}

/** All discarded pids as a Set. */
export async function loadDiscards() {
  const rows = await getAll('discards');
  return new Set(rows.map((r) => r.pid));
}

export const saveSrs = (row) => put('srs', row);
export const discard = (pid, at) => put('discards', { pid, at });

export async function logAttempt(pid, at, uci, correct, tookMs) {
  const id = `${await deviceId()}:${uuid()}`;
  return put('attempts', { id, pid, at, uci, correct: correct ? 1 : 0, took_ms: tookMs });
}

export const countAttempts = () => tx('attempts', 'readonly', (s) => s.count());

/* ---------- device identity ----------

   Sync gives every device its own file in the gist, so each one needs a name
   that survives reloads. The id is also the prefix of every attempt id this
   device writes, which is what partitions the append-only log across peers. */

const uuid = () => (crypto.randomUUID
  ? crypto.randomUUID()
  : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`);

function guessLabel() {
  const ua = navigator.userAgent;
  if (/iPad|Tablet/i.test(ua)) return 'tablet';
  if (/Mobi|Android|iPhone/i.test(ua)) return 'phone';
  return 'desktop';
}

let deviceIdPromise = null;

export function deviceId() {
  if (deviceIdPromise) return deviceIdPromise;
  deviceIdPromise = (async () => {
    let id = await getMeta('device_id', null);
    if (!id) {
      id = uuid().replace(/-/g, '').slice(0, 8);
      await setMeta('device_id', id);
      await setMeta('device_label', guessLabel());
    }
    return id;
  })();
  return deviceIdPromise;
}

export const deviceLabel = () => getMeta('device_label', guessLabel());
export const setDeviceLabel = (label) => setMeta('device_label', label);

/* Attempts predating sync were keyed by autoIncrement, so their ids collide
   across devices and cannot survive a round trip. Re-key them once, under this
   device's prefix — they were all recorded here. Explicit string keys and the
   store's autoIncrement coexist fine, so this needs no schema version bump. */
export async function migrateAttemptIds() {
  const rows = await getAll('attempts');
  const legacy = rows.filter((r) => typeof r.id !== 'string');
  if (!legacy.length) return 0;
  const device = await deviceId();
  await tx('attempts', 'readwrite', (s) => {
    for (const row of legacy) {
      s.delete(row.id);
      s.add({ ...row, id: `${device}:legacy:${row.id}` });
    }
  });
  return legacy.length;
}

/** Port of srs.reset_training: wipe practice history, keep discards.

    The `reset_at` stamp is what makes this survive sync: without it the next
    pull would restore the schedule from a peer that had not heard about the
    reset. Peers drop everything older than the newest stamp they see. */
export async function resetTraining() {
  const attempts = await countAttempts();
  const srs = (await getAll('srs')).length;
  await clear('attempts');
  await clear('srs');
  await setMeta('reset_at', Math.floor(Date.now() / 1000));
  return { attempts_deleted: attempts, scheduling_deleted: srs };
}

/* ---------- eviction defence ---------- */

/** Ask Chrome to exempt this origin from LRU eviction. Installed PWAs get it. */
export async function requestPersistence() {
  if (!navigator.storage?.persist) return null;
  if (await navigator.storage.persisted()) return true;
  try {
    return await navigator.storage.persist();
  } catch {
    return false;
  }
}

export async function storageEstimate() {
  if (!navigator.storage?.estimate) return null;
  try {
    return await navigator.storage.estimate();
  } catch {
    return null;
  }
}
