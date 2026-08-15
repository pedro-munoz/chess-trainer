/* Static-mode API: everything the FastAPI backend did, in the browser.

   Puzzle selection, filters and SRS are ports of chess_trainer/web.py and
   srs.py. Answer checking uses the accept sets baked by chess_trainer/accept.py
   at export time, so a verdict here is the one the desktop engine gave — not
   one a phone-sized search improvised. The bundled engine is only consulted
   for free exploration, and for the rare puzzle whose accept set is marked
   incomplete. */

import * as data from './data.js';
import * as store from './store.js';
import * as srs from './srs.js';
import * as rules from './rules.js';
import { fmtEval, scoreToCp, winPct } from './judgments.js';

let state = null;

export async function ready() {
  if (state) return state;
  const d = await data.load();
  const [srsMap, discards] = await Promise.all([store.loadSrs(), store.loadDiscards()]);
  state = { d, srs: srsMap, discards };
  await reconcile(state);
  store.requestPersistence();
  return state;
}

/* ---------- dataset drift ----------

   Redeploys are routine: Pedro re-analyzes on the laptop and republishes, while
   the schedule lives only here. Puzzles are matched by pid, and each carries a
   content hash `h` over (fen, best move, judgment). A puzzle whose answer
   changed is worth relearning, so its schedule resets — but its lapse count is
   evidence about Pedro, not about the puzzle, so that survives. A puzzle that
   vanished is tombstoned rather than deleted: re-analysis may bring it back. */
async function reconcile(s) {
  const now = Math.floor(Date.now() / 1000);
  const writes = [];
  for (const row of s.srs.values()) {
    const puzzle = s.d.byPid.get(row.pid);
    if (!puzzle) {
      if (!row.orphan_since) { row.orphan_since = now; writes.push(row); }
      continue;
    }
    if (row.orphan_since) { delete row.orphan_since; writes.push(row); }
    if (row.h && row.h !== puzzle.h) {
      Object.assign(row, {
        h: puzzle.h, ease: row.ease, lapses: row.lapses,
        reps: 0, interval_days: 0, due_at: 0,
      });
      writes.push(row);
    }
  }
  if (writes.length) await store.putMany('srs', writes);
  await store.setMeta('build_id', s.d.manifest.build_id);
}

/* ---------- selection ---------- */

const csv = (v) => String(v || '').split(',').map((x) => x.trim()).filter(Boolean);
const gameIdOf = (pid) => pid.slice(0, pid.lastIndexOf(':'));

/** Port of web.py::next_puzzle's WHERE clause. */
function candidates(s, params, now) {
  const kinds = csv(params.judgments_filter);
  const phases = csv(params.phases).filter(
    (p) => ['opening', 'middlegame', 'endgame'].includes(p));
  const motifs = csv(params.motif);
  const endgames = csv(params.endgame);
  const { color, opening } = params;

  return s.d.puzzles.filter((p) => {
    if (s.discards.has(p.p)) return false;
    const sched = s.srs.get(p.p);
    if (sched && sched.due_at > now) return false;
    if (kinds.length && !kinds.includes(p.j)) return false;
    if (phases.length && !phases.includes(p.ph)) return false;
    if (color === 'white' || color === 'black') {
      if (p.c !== color) return false;
    }
    if (opening) {
      const op = s.d.games[gameIdOf(p.p)]?.op || '';
      if (op !== opening && !op.startsWith(opening + ':')) return false;
    }
    if (motifs.length) {
      // "positional" also covers rows predating the motif backfill (NULL).
      const motif = p.mo ?? 'positional';
      if (!motifs.includes(motif)) return false;
    }
    if (endgames.length) {
      if (p.ph !== 'endgame') return false;
      if (!endgames.includes(p.eg ?? rules.classifyEndgame(p.f))) return false;
    }
    return true;
  });
}

export async function getNext(params = {}) {
  const s = await ready();
  const now = Math.floor(Date.now() / 1000);
  const pool = candidates(s, params, now);

  if (!pool.length) {
    // Soonest due among non-discarded puzzles, matching web.py's empty response.
    let soonest = null;
    for (const row of s.srs.values()) {
      if (s.discards.has(row.pid) || !s.d.byPid.has(row.pid)) continue;
      if (row.due_at > now && (soonest === null || row.due_at < soonest)) soonest = row.due_at;
    }
    return { puzzle: null, next_due_in_s: soonest === null ? null : soonest - now };
  }

  let picked;
  if (Number(params.focus)) {
    // Port of the focus weighting: costly mistakes and repeat lapses first.
    const weights = pool.map(
      (p) => p.wl + 15 * (s.srs.get(p.p)?.lapses || 0));
    const total = weights.reduce((a, b) => a + b, 0);
    let r = Math.random() * total;
    picked = pool[pool.length - 1];
    for (let i = 0; i < pool.length; i++) {
      r -= weights[i];
      if (r <= 0) { picked = pool[i]; break; }
    }
  } else {
    picked = pool[Math.floor(Math.random() * pool.length)];
  }

  const chess = new rules.Chess(picked.f);
  return {
    puzzle: data.toPuzzlePayload(picked, s.d.games, rules.dests(chess)),
    next_due_in_s: null,
  };
}

/* ---------- answering ---------- */

export async function postAttempt({ pid, move_uci: uci, took_ms: tookMs, retry }) {
  const s = await ready();
  const p = s.d.byPid.get(pid);
  if (!p) return null;

  const chess = new rules.Chess(p.f);
  const pov = chess.turn();
  const tolerance = s.d.manifest.tolerance_winpct;

  let correct;
  let playedSan = null;
  let attemptWin = null;
  let attemptEval = null;

  if (!uci) {
    correct = false;
  } else {
    const move = rules.parseMove(chess, uci);
    if (!move) return null;                      // illegal: web.py raises 400
    const normalized = move.from + move.to + (move.promotion || '');
    if (uci === p.bu || normalized === p.bu) {
      correct = true;
      playedSan = p.bs;
    } else {
      playedSan = move.san;
      const baked = (p.mv || []).find(([u]) => u === normalized);
      if (baked) {
        const [, cp, mate] = baked;
        attemptWin = winPct(scoreToCp(cp, mate), pov);
        attemptEval = fmtEval(cp, mate, pov);
        correct = attemptWin >= p.wb - tolerance;
      } else if (p.mvc) {
        // The accept set is provably exhaustive: an unlisted move is wrong.
        correct = false;
      } else {
        const { evaluatePosition } = await import('./engine.js');
        const after = new rules.Chess(p.f);
        rules.play(after, move);
        const ev = await evaluatePosition(after.fen());
        if (ev) {
          attemptWin = winPct(scoreToCp(ev.cp, ev.mate), pov);
          attemptEval = fmtEval(ev.cp, ev.mate, pov);
          correct = attemptWin >= p.wb - tolerance;
        } else {
          correct = false;
        }
      }
    }
  }

  const now = Math.floor(Date.now() / 1000);
  await store.logAttempt(pid, now, uci, correct, tookMs);

  let sched = null;
  if (!retry) {
    const prev = s.srs.get(pid);
    sched = srs.review(prev, correct, now);
    const row = { pid, ...sched, h: p.h, last_at: now };
    s.srs.set(pid, row);
    await store.saveSrs(row);
  }

  return {
    correct,
    your_move_san: playedSan,
    your_move_win_pct: attemptWin === null ? null : Math.round(attemptWin * 10) / 10,
    eval_yours: attemptEval,
    eval_best: p.ev ?? null,
    win_best: p.wb,
    best_san: p.bs,
    best_uci: p.bu,
    pv_san: p.pv ?? null,
    explanation: p.e ?? null,
    explanation_played: p.ep ?? null,
    game_played_san: p.ps,
    win_loss: p.wl,
    scheduling: sched,
  };
}

export async function postDiscard(pid) {
  const s = await ready();
  s.discards.add(pid);
  await store.discard(pid, Math.floor(Date.now() / 1000));
  return { ok: true };
}

/* ---------- exploration ---------- */

export async function postEval({ fen, move_uci: uci }) {
  const { evalPosition } = await import('./engine.js');
  return evalPosition(fen, uci);
}

/** Where a move leads, without evaluating it — no engine, so it is instant.

    Enough to keep playing on the board after a solve; the engine only gets
    involved once there is a position worth an opinion. */
export async function positionAfter(fen, moveUci) {
  const chess = new rules.Chess(fen);
  let san = null;
  if (moveUci) {
    const move = rules.parseMove(chess, moveUci);
    if (!move) return null;
    san = rules.play(chess, move).san;
  }
  const over = rules.gameOver(chess);
  return {
    fen: chess.fen(),
    turn: chess.turn() === 'w' ? 'white' : 'black',
    dests: rules.dests(chess),
    played_san: san,
    game_over: over ? over.text : null,
    winner: over ? over.winner : null,
  };
}

/* The bundled engine is single-threaded wasm: a search costs ~0.5-1s on a
   phone, so reveals render from stored data and the engine waits until the
   user actually explores. */
export const deferEngine = true;
export const backupSupported = true;

/* ---------- dashboard ---------- */

export async function getStats() {
  const s = await ready();
  const now = Math.floor(Date.now() / 1000);
  const live = s.d.puzzles.filter((p) => !s.discards.has(p.p));

  const mistakes = {};
  let due = 0;
  let fresh = 0;
  for (const p of live) {
    mistakes[p.j] = (mistakes[p.j] || 0) + 1;
    const sched = s.srs.get(p.p);
    if (!sched) { fresh++; due++; } else if (sched.due_at <= now) due++;
  }

  const counts = s.d.manifest.counts;
  return {
    username: s.d.manifest.username,
    games: { total: counts.games, analyzed: counts.games_analyzed,
             pending: counts.games - counts.games_analyzed },
    mistakes,
    by_phase: [],
    by_color: [],
    due,
    new: fresh,
    attempts: await store.countAttempts(),
  };
}

export async function getInsights() {
  const s = await ready();
  return s.d.insights;
}

export async function resetTraining() {
  const s = await ready();
  const result = await store.resetTraining();
  s.srs.clear();
  return { ok: true, ...result };
}
