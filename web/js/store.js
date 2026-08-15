/* Local training state for the static build (IndexedDB).

   In the static build this replaces the server's `attempts` and `scheduling`
   tables: the phone is the only place training happens, so this is the only
   copy that exists. Hence backup.js, and hence navigator.storage.persist().

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

export const logAttempt = (pid, at, uci, correct, tookMs) =>
  tx('attempts', 'readwrite',
     (s) => s.add({ pid, at, uci, correct: correct ? 1 : 0, took_ms: tookMs }));

export const countAttempts = () => tx('attempts', 'readonly', (s) => s.count());

/** Port of srs.reset_training: wipe practice history, keep discards. */
export async function resetTraining() {
  const attempts = await countAttempts();
  const srs = (await getAll('srs')).length;
  await clear('attempts');
  await clear('srs');
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
