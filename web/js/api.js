/* The API contract shared by both builds.

   Both backends return exactly the shapes the FastAPI app returns, so the UI
   code (train.js, insights.js, dashboard.js) is identical in either mode.

     getStats()                                  -> /api/stats shape
     getNext(params)                             -> {puzzle, next_due_in_s}
     postAttempt({pid, move_uci, took_ms, retry})-> /api/attempt shape (null if illegal)
     postEval({fen, move_uci})                   -> /api/eval shape (null on failure)
     postDiscard(pid)                            -> {ok: true}
     getInsights()                               -> compute_insights() shape
     resetTraining()                             -> {ok, attempts_deleted, scheduling_deleted}
     ready()                                     -> resolves once local data is loaded

   Mode is fixed at build time by config.js. The static import below is
   deliberately not dynamic: a bundler-free ESM page can import both and pick,
   but that would download both backends. */

import { MODE } from './config.js';

const impl = MODE === 'static'
  ? await import('./api.local.js')
  : await import('./api.server.js');

export const {
  getStats, getNext, postAttempt, postEval, postDiscard, getInsights,
  resetTraining, ready, deferEngine, backupSupported, positionAfter,
} = impl;
