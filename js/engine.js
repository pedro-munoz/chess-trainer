/* Stockfish (WASM) client for the static build.

   One long-lived Worker — instantiating the 7 MB module is the expensive part,
   so it is never torn down between positions. UCI goes over postMessage as
   plain strings and comes back the same way.

   Threads stay at 1 (this is the single-threaded build anyway): `go nodes N` is
   only deterministic for a fixed thread count, and a verdict that depends on
   how busy the phone is would be worse than no verdict.

   Node budgets are calibrated per device on first use. A desktop's 400k-node
   search is 1.5-3s on a mid-range phone, which is far too slow to sit behind a
   tap; ~600ms is the target. */

import * as rules from './rules.js';
import { winPct } from './judgments.js';

const ENGINE_JS = '../vendor/stockfish/stockfish-18-lite-single.js';

const TARGET_MS = 600;
const DEFAULT_NODES = 120_000;
const MIN_NODES = 40_000;
const MAX_NODES = 400_000;
const CALIBRATION_NODES = 200_000;
const SEARCH_TIMEOUT_MS = 20_000;
const NODES_KEY = 'trainer-engine-nodes';

let worker = null;
let bootPromise = null;
let listeners = [];
let nodeBudget = Number(localStorage.getItem(NODES_KEY)) || DEFAULT_NODES;
let queue = Promise.resolve();

function onLine(line) {
  for (const fn of listeners.slice()) fn(line);
}

/** Resolve once `predicate` accepts a line; collects every line meanwhile. */
function waitFor(predicate, timeoutMs = SEARCH_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const lines = [];
    const timer = setTimeout(() => { detach(); reject(new Error('engine timeout')); }, timeoutMs);
    const listener = (line) => {
      lines.push(line);
      if (predicate(line)) { detach(); resolve(lines); }
    };
    const detach = () => {
      clearTimeout(timer);
      listeners = listeners.filter((l) => l !== listener);
    };
    listeners.push(listener);
  });
}

const send = (cmd) => worker.postMessage(cmd);

function boot() {
  if (bootPromise) return bootPromise;
  bootPromise = (async () => {
    // The loader finds its .wasm by swapping the extension on its own URL,
    // which is exactly the layout here. (The documented "#<wasm-url>" override
    // silently stops the module from ever reporting uciok — don't use it.)
    worker = new Worker(new URL(ENGINE_JS, import.meta.url).href);
    worker.onmessage = (e) => {
      const line = typeof e.data === 'string' ? e.data : e.data?.data;
      if (typeof line === 'string') onLine(line);
    };

    send('uci');
    await waitFor((l) => l === 'uciok', 60_000);
    send('setoption name Threads value 1');
    send('setoption name Hash value 32');
    send('isready');
    await waitFor((l) => l === 'readyok', 60_000);

    if (!localStorage.getItem(NODES_KEY)) await calibrate();
    return worker;
  })().catch((err) => { bootPromise = null; throw err; });
  return bootPromise;
}

/** Measure this device's nps once, and size every later search from it. */
async function calibrate() {
  try {
    send('ucinewgame');
    send('position startpos');
    send(`go nodes ${CALIBRATION_NODES}`);
    const lines = await waitFor((l) => l.startsWith('bestmove'), 60_000);
    let nps = 0;
    for (const line of lines) {
      const m = /\bnps (\d+)/.exec(line);
      if (m) nps = Number(m[1]);
    }
    if (nps > 0) {
      nodeBudget = Math.round(
        Math.min(MAX_NODES, Math.max(MIN_NODES, (nps * TARGET_MS) / 1000)));
      localStorage.setItem(NODES_KEY, String(nodeBudget));
    }
  } catch {
    // Keep the conservative default; a slow calibration is not worth failing on.
  }
}

/** Serialize searches: one engine, one search at a time. */
function withEngine(fn) {
  const run = queue.then(async () => {
    await boot();
    return fn();
  });
  queue = run.catch(() => {});
  return run;
}

function parseInfo(lines) {
  // Keep the deepest completed line per multipv index; drop bound-flagged ones
  // (they are mid-window artifacts, not evaluations).
  const best = new Map();
  for (const line of lines) {
    if (!line.startsWith('info ') || !line.includes(' pv ')) continue;
    if (line.includes('lowerbound') || line.includes('upperbound')) continue;
    const depth = Number(/\bdepth (\d+)/.exec(line)?.[1] || 0);
    const idx = Number(/\bmultipv (\d+)/.exec(line)?.[1] || 1);
    const cpM = /\bscore cp (-?\d+)/.exec(line);
    const mateM = /\bscore mate (-?\d+)/.exec(line);
    const pv = line.slice(line.indexOf(' pv ') + 4).trim().split(/\s+/);
    const prev = best.get(idx);
    if (prev && prev.depth > depth) continue;
    best.set(idx, {
      depth,
      cp: cpM ? Number(cpM[1]) : null,
      mate: mateM ? Number(mateM[1]) : null,
      pv,
    });
  }
  return [...best.entries()].sort((a, b) => a[0] - b[0]).map(([, v]) => v);
}

async function search(fen, multipv) {
  send(`setoption name MultiPV value ${multipv}`);
  send('ucinewgame');
  send('isready');
  await waitFor((l) => l === 'readyok');
  send(`position fen ${fen}`);
  send(`go nodes ${nodeBudget}`);
  const lines = await waitFor((l) => l.startsWith('bestmove'));
  return parseInfo(lines);
}

/** Scores come back from the side to move; the rest of the app is white-relative. */
function toWhite(entry, turn) {
  const flip = turn === 'b' ? -1 : 1;
  return {
    cp: entry.cp === null ? null : entry.cp * flip,
    mate: entry.mate === null ? null : entry.mate * flip,
  };
}

/** White-relative {cp, mate} for a position — used to judge an unlisted move. */
export async function evaluatePosition(fen) {
  return withEngine(async () => {
    const chess = new rules.Chess(fen);
    const over = rules.gameOver(chess);
    if (over) {
      return over.winner === null
        ? { cp: 0, mate: null }
        : { cp: null, mate: over.winner === 'white' ? 1 : -1 };
    }
    const lines = await search(fen, 1);
    return lines.length ? toWhite(lines[0], chess.turn()) : null;
  }).catch(() => null);
}

/** Same shape as the server's POST /api/eval, so train.js needs no branch. */
export async function evalPosition(fen, moveUci = null) {
  return withEngine(async () => {
    const chess = new rules.Chess(fen);
    let playedSan = null;
    if (moveUci) {
      const move = rules.parseMove(chess, moveUci);
      if (!move) return null;
      playedSan = rules.play(chess, move).san;
    }

    const nextFen = chess.fen();
    const turn = chess.turn() === 'w' ? 'white' : 'black';
    const over = rules.gameOver(chess);
    if (over) {
      return {
        fen: nextFen, turn, dests: {}, played_san: playedSan,
        game_over: over.text, winner: over.winner, lines: [],
      };
    }

    const found = await search(nextFen, 2);
    const lines = found.slice(0, 2).map((entry) => {
      const { cp, mate } = toWhite(entry, chess.turn());
      return {
        cp,
        mate,
        win_white: winPct(mate !== null ? (mate > 0 ? 10000 : -10000) : cp, 'w'),
        first_uci: entry.pv[0] || null,
        pv_san: rules.sanLine(nextFen, entry.pv, 12),
      };
    });

    return {
      fen: nextFen, turn, dests: rules.dests(chess), played_san: playedSan,
      game_over: null, winner: null, lines,
    };
  }).catch(() => null);
}

export const nodes = () => nodeBudget;
