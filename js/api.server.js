/* Server-mode API: the FastAPI backend, unchanged.

   This is the authoring/analysis environment — `python -m chess_trainer serve`.
   The static build swaps in api.local.js instead; see api.js. */

async function post(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return resp;
}

async function postJson(path, body) {
  const resp = await post(path, body);
  if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
  return resp.json();
}

export const getStats = () => fetch('/api/stats').then((r) => r.json());
export const getInsights = () => fetch('/api/insights').then((r) => r.json());

export function getNext(params) {
  const q = new URLSearchParams(params);
  return fetch('/api/next?' + q).then((r) => r.json());
}

export async function postAttempt(body) {
  const resp = await post('/api/attempt', body);
  if (!resp.ok) return null;   // 400 = illegal move; train.js treats null as "ignore"
  return resp.json();
}

export const postEval = (body) => post('/api/eval', body)
  .then((r) => (r.ok ? r.json() : null));

export const postDiscard = (pid) => postJson('/api/discard', { pid });
export const resetTraining = () => postJson('/api/reset-training', { confirm: true });

/* Capability flags. The desktop engine answers in ~0.2s, so the trainer can
   afford to evaluate every reveal; the phone's wasm engine cannot. */
export const ready = () => Promise.resolve();
export const deferEngine = false;
export const backupSupported = false;
export const syncSupported = false;
// Only needed when the engine is deferred, which it never is here.
export const positionAfter = () => Promise.resolve(null);
