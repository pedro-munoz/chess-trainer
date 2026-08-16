/* "Learn from your mistakes" trainer. */

import { Chessground as CG } from '../vendor/chessground/chessground.min.js';
import * as api from './api.js';

let cg = null;
let puzzle = null;
let solvedOrRevealed = false;   // answer shown; board belongs to explore mode
let firstTryDone = false;       // SRS outcome already decided for this puzzle
let startedAt = 0;
let lastBest = null;
let exploring = false;
let history = [];               // [{fen, data}] explore positions, data = /api/eval result
let histIdx = -1;
const session = { solved: 0, failed: 0, streak: 0 };

/* The trail of puzzles shown this session, so "Previous" can walk back through
   them. Each entry carries the state the puzzle was left in — a revealed one
   comes back revealed, an unanswered one comes back unanswered — because the
   SRS outcome is already decided and re-solving it must not count twice. */
let seen = [];
let seenIdx = -1;
let cur = null;                 // seen[seenIdx], or null while the empty state shows
let pendingEmpty = false;       // "no puzzles due" is on screen; it is not in `seen`

const el = (id) => document.getElementById(id);

/* Snap the board to a multiple of 16 CSS pixels so squares land on whole
   physical pixels even at fractional display scaling. */
const frame = document.querySelector('.board-frame');
function fitBoard() {
  const barW = el('evalbar').classList.contains('active') ? 24 : 0;
  const size = Math.max(160, Math.floor((frame.clientWidth - 20 - barW) / 16) * 16);
  const b = el('board');
  b.style.width = size + 'px';
  b.style.height = size + 'px';
  b.style.aspectRatio = 'auto';
  // Rank/file labels are sized off the square, not the page: fixed type is lost
  // on a big board and crowds a small one. Kept deliberately small and capped,
  // the way lichess's are — they are a reference, not a feature.
  const coord = Math.min(12, Math.max(8, Math.round((size / 8) * 0.14)));
  b.style.setProperty('--coord-size', coord + 'px');
  if (cg) cg.redrawAll();
}
new ResizeObserver(fitBoard).observe(frame);
window.addEventListener('resize', fitBoard);
fitBoard();

function filters() {
  const kinds = [...document.querySelectorAll('#filters input[name=judgment]:checked')]
    .map((c) => c.value);
  const phases = [...document.querySelectorAll('#filters input[name=phase]:checked')]
    .map((c) => c.value);
  const color = document.querySelector('#filters input[name=color]:checked').value;
  return { kinds, phases, color };
}

/* URL-launched sessions (from the Insights page): judgments/phases/color pre-set
   the visible filters; opening/motif/focus have no UI and ride along as-is. */
const urlq = new URLSearchParams(location.search);
const extra = {};
for (const k of ['opening', 'motif', 'endgame', 'focus']) {
  const v = urlq.get(k);
  if (v) extra[k] = v;
}

function applyUrlFilters() {
  const setChecks = (name, csv) => {
    const values = csv.split(',').map((s) => s.trim()).filter(Boolean);
    document.querySelectorAll(`#filters input[name=${name}]`).forEach((i) => {
      i.checked = values.includes(i.value);
    });
  };
  if (urlq.get('judgments')) setChecks('judgment', urlq.get('judgments'));
  if (urlq.get('phases')) setChecks('phase', urlq.get('phases'));
  const color = urlq.get('color');
  if (color !== null
      && document.querySelector(`#filters input[name=color][value="${color}"]`)) {
    document.querySelector(`#filters input[name=color][value="${color}"]`).checked = true;
  }
}

function renderFocusPill() {
  const pill = el('focus-pill');
  const parts = [];
  if (extra.opening) parts.push(extra.opening);
  if (extra.motif) parts.push(extra.motif.split(',').map((m) => m.replace(/_/g, ' ')).join(' + '));
  if (extra.endgame) parts.push(extra.endgame + ' endgames');
  if (extra.focus) parts.push('weighted by severity');
  if (!parts.length) { pill.style.display = 'none'; return; }
  pill.style.display = '';
  pill.innerHTML = `<span>Focus: ${parts.join(' · ')}</span><button title="Clear focus">&#10005;</button>`;
  pill.querySelector('button').addEventListener('click', () => {
    Object.keys(extra).forEach((k) => delete extra[k]);
    window.history.replaceState(null, '', location.pathname); // `history` is the explore stack
    renderFocusPill();
    loadPuzzle({ replace: true });
  });
}

const fmtDate = (ms) => new Date(ms).toISOString().slice(0, 10);

function renderSession() {
  el('s-solved').textContent = session.solved;
  el('s-failed').textContent = session.failed;
  el('s-streak').textContent = session.streak;
}

function setEvalBar(winWhite) {
  const bar = el('evalbar');
  const fill = el('evalbar-fill');
  bar.classList.add('active');
  const pct = winWhite === null ? 50 : winWhite;
  fill.style.height = pct + '%';
  // White's share grows from White's side of the board.
  if (puzzle.color === 'white') { fill.style.bottom = '0'; fill.style.top = 'auto'; }
  else { fill.style.top = '0'; fill.style.bottom = 'auto'; }
  fitBoard();
}

function hideEvalBar() {
  el('evalbar').classList.remove('active');
  fitBoard();
}

/* ---------- puzzle flow ---------- */

function resetView() {
  exploring = false;
  history = [];
  histIdx = -1;
  hideEvalBar();
  el('board-nav').classList.remove('active');
  el('feedback').className = 'feedback';
  el('feedback').style.display = 'none';
  el('giveup').disabled = false;
  el('discard').disabled = false;
}

/* Keep the trail in step with the puzzle on screen: a wrong first try decides
   the SRS outcome even if you move on, so coming back must not offer another
   first try. Whether the answer is out is `reveal` — no separate flag. */
function saveState() {
  if (cur) cur.firstTryDone = firstTryDone;
}

function renderNav() {
  el('prev').disabled = pendingEmpty ? seen.length === 0 : seenIdx <= 0;
}

function renderPuzzleText() {
  el('tags').innerHTML = '';  // judgment and motif would give the answer away
  el('prompt').innerHTML =
    `${puzzle.color === 'white' ? 'White' : 'Black'} to play — you chose ` +
    `<span class="move">${puzzle.played_san}</span>. Find a better move.`;
  const g = puzzle.game;
  el('meta').innerHTML =
    `<span>${g.speed} vs ${g.opponent}${g.opponent_rating ? ' (' + g.opponent_rating + ')' : ''}</span>` +
    `<span>${fmtDate(g.played_at)}</span>` +
    `<a href="${g.url}" target="_blank">view game &nearr;</a>`;
}

function showEntry(entry) {
  cur = entry;
  pendingEmpty = false;
  puzzle = entry.puzzle;
  firstTryDone = entry.firstTryDone;
  solvedOrRevealed = false;   // reveal() sets it again for an answered puzzle
  // The clock is for this visit: a puzzle left open while you looked at others
  // did not take you an hour to think about.
  startedAt = Date.now();
  resetView();
  renderPuzzleText();
  setPuzzleBoard();
  if (entry.reveal) reveal(entry.reveal, entry.solvedUci);
  renderNav();
}

async function loadPuzzle({ replace = false } = {}) {
  solvedOrRevealed = false;
  firstTryDone = false;
  resetView();

  const { kinds, phases, color } = filters();
  const params = {};
  if (kinds.length) params.judgments_filter = kinds.join(',');
  if (phases.length && phases.length < 3) params.phases = phases.join(',');
  if (color) params.color = color;
  Object.assign(params, extra);
  const data = await api.getNext(params);

  if (!data.puzzle) {
    puzzle = null;
    cur = null;
    pendingEmpty = true;
    el('tags').innerHTML = '';
    el('prompt').innerHTML = '<span class="empty">No puzzles due with these filters.'
      + (data.next_due_in_s != null
        ? ` Next one due in ${Math.ceil(data.next_due_in_s / 60)} min.` : '')
      + '</span>';
    el('meta').textContent = '';
    if (cg) cg.set({ viewOnly: true });
    renderNav();
    return;
  }

  const entry = {
    puzzle: data.puzzle, firstTryDone: false, reveal: null, solvedUci: null,
  };
  // A new puzzle always lands at the end of the trail; anything ahead of the
  // current position was reached under other circumstances and is dropped.
  seen.length = Math.max(0, seenIdx + (replace ? 0 : 1));
  seen.push(entry);
  seenIdx = seen.length - 1;
  showEntry(entry);
}

const RETURN_MS = 260;   // how long the rejected piece takes to glide back
const HOLD_MS = 420;     // how long it stays put first, so you see what you played

function setPuzzleBoard({ glide = false } = {}) {
  const config = {
    fen: puzzle.fen,
    orientation: puzzle.color,
    turnColor: puzzle.color,
    check: false,
    lastMove: undefined,
    viewOnly: false,
    animation: { enabled: true, duration: glide ? RETURN_MS : 0 },
    movable: {
      free: false,
      color: puzzle.color,
      dests: new Map(Object.entries(puzzle.dests)),
      events: { after: onMove },
    },
  };
  if (!cg) cg = CG(el('board'), config);
  else cg.set(config);
  cg.setAutoShapes([{
    orig: puzzle.played_uci.slice(0, 2),
    dest: puzzle.played_uci.slice(2, 4),
    brush: 'red',
  }]);
  // This chessground build doesn't repaint shapes on its own, but redrawAll()
  // is a synchronous full repaint — calling it now would cancel the animation
  // we just asked for, which is what made the piece snap back instantly.
  if (glide) setTimeout(() => cg.redrawAll(), RETURN_MS);
  else cg.redrawAll();
}

function isPromotion(orig, dest) {
  const piece = cg.state.pieces.get(dest); // after move, piece is on dest
  return piece && piece.role === 'pawn' && (dest[1] === '1' || dest[1] === '8');
}

async function onMove(orig, dest) {
  if (!puzzle) return;
  let uci = orig + dest;
  if (isPromotion(orig, dest)) uci += 'q'; // v1: auto-queen
  if (exploring) { await exploreMove(uci); return; }
  if (solvedOrRevealed) return;
  await submit(uci);
}

async function submit(uci) {
  const r = await api.postAttempt({
    pid: puzzle.pid,
    move_uci: uci,
    took_ms: Date.now() - startedAt,
    retry: firstTryDone,
  });
  if (!r) return;   // illegal move: leave the board as it is

  if (!firstTryDone) {
    firstTryDone = true;
    if (r.correct) { session.solved++; session.streak++; }
    else { session.failed++; session.streak = 0; }
    renderSession();
  }
  saveState();

  if (r.correct || uci === null) {
    reveal(r, r.correct ? uci : null);
  } else {
    retryFeedback(r);
  }
}

function retryFeedback(r) {
  const fb = el('feedback');
  fb.style.display = 'block';
  fb.className = 'feedback wrong';
  let msg = `✗ ${r.your_move_san} isn't the best move — try again.`;
  if (r.eval_yours !== null && r.eval_best !== null) {
    msg = `✗ ${r.your_move_san} drops the evaluation to ${r.eval_yours}; `
        + `the best move keeps ${r.eval_best}. Try again.`;
  }
  el('fb-title').textContent = msg;
  el('fb-good').style.display = 'none';
  el('fb-bad').style.display = 'none';
  el('fb-line').style.display = 'none';
  // Leave the rejected move on the board for a beat, then glide the piece home.
  // Yanking it back the instant it lands reads as the board refusing the input
  // rather than as "that move was wrong".
  setTimeout(() => { if (puzzle && !solvedOrRevealed) setPuzzleBoard({ glide: true }); },
             HOLD_MS);
}

/* Shown only once the answer is out: "blunder" hints at how much is at stake and
   the motif names the tactic outright, so both are clues while you are solving. */
function renderTags() {
  el('tags').innerHTML =
    `<span class="badge ${puzzle.judgment}">${puzzle.judgment}</span>` +
    `<span class="badge phase">${puzzle.phase}</span>` +
    `<span class="badge phase">move ${puzzle.move_number}</span>` +
    (puzzle.motif && puzzle.motif !== 'positional'
      ? `<span class="badge phase">${puzzle.motif.replace(/_/g, ' ')}</span>` : '');
}

function trimLine(san, plies = 8) {
  if (!san) return '';
  const parts = san.split(' ');
  return parts.slice(0, plies).join(' ') + (parts.length > plies ? ' …' : '');
}

function reveal(r, solvedUci) {
  solvedOrRevealed = true;
  el('giveup').disabled = true;
  lastBest = r.best_uci;
  if (cur) { cur.reveal = r; cur.solvedUci = solvedUci; }
  saveState();
  renderTags();

  const fb = el('feedback');
  fb.style.display = 'block';
  fb.className = 'feedback ' + (r.correct ? 'correct' : 'wrong');
  if (r.correct) {
    el('fb-title').textContent =
      r.your_move_san === r.best_san
        ? `✓ Correct — ${r.best_san} is the engine's move.`
        : `✓ Good move! ${r.your_move_san} works too (engine prefers ${r.best_san}).`;
  } else {
    el('fb-title').textContent = `✗ The best move was ${r.best_san}.`;
  }
  el('fb-good').style.display = r.explanation ? 'flex' : 'none';
  el('fb-good-text').textContent = r.explanation || '';
  el('fb-bad').style.display = r.explanation_played ? 'flex' : 'none';
  el('fb-bad-text').textContent = r.explanation_played || '';
  el('fb-line').style.display = r.pv_san ? 'block' : 'none';
  el('fb-line').textContent = trimLine(r.pv_san);

  enterExplore(solvedUci, r);
}

/* ---------- explore mode (after reveal) ---------- */

function fmtEval(line) {
  if (!line) return { text: '—', pct: 50 };
  if (line.mate !== null) {
    return { text: (line.mate > 0 ? '+M' : '-M') + Math.abs(line.mate), pct: line.mate > 0 ? 100 : 0 };
  }
  const pawns = line.cp / 100;
  return { text: (pawns > 0 ? '+' : '') + pawns.toFixed(1), pct: line.win_white };
}

async function evalRequest(body) {
  el('ex-eval').textContent = '…';
  return api.postEval(body);
}

function puzzleArrows() {
  return [
    { orig: lastBest.slice(0, 2), dest: lastBest.slice(2, 4), brush: 'green' },
    { orig: puzzle.played_uci.slice(0, 2), dest: puzzle.played_uci.slice(2, 4), brush: 'red' },
  ];
}

/* Grey on-board arrows for the engine's top lines; width scales with how
   strong each move is relative to the best one (Lichess-style). */
function engineArrows(lines) {
  if (!lines || !lines.length) return [];
  const best = fmtEval(lines[0]).pct;
  return lines.slice(0, 2).map((l, i) => {
    if (!l.first_uci) return null;
    const gap = i === 0 ? 0 : Math.abs(best - fmtEval(l).pct);
    const width = Math.max(4, Math.round(11 * Math.max(0.35, 1 - gap / 25)));
    return {
      orig: l.first_uci.slice(0, 2),
      dest: l.first_uci.slice(2, 4),
      brush: 'paleGrey',
      modifiers: { lineWidth: width },
    };
  }).filter(Boolean);
}

/* A finished game is never even: the bar goes all the way to the winner
   (and stays centred only for a genuine draw). */
function finalPct(winner) {
  if (winner === 'white') return 100;
  if (winner === 'black') return 0;
  return 50;
}

function renderExplore(data, { arrows = null } = {}) {
  const top = data.lines[0] || null;
  const ev = fmtEval(top);
  el('ex-eval').textContent = data.game_over ? data.game_over : ev.text;
  setEvalBar(data.game_over ? finalPct(data.winner) : ev.pct);
  cg.set({
    fen: data.fen,
    turnColor: data.turn,
    check: false,
    movable: {
      free: false,
      color: data.turn,
      dests: new Map(Object.entries(data.dests)),
      events: { after: onMove },
    },
  });
  let shapes = engineArrows(data.lines);
  if (arrows) {
    const keys = new Set(arrows.map((a) => a.orig + a.dest));
    shapes = arrows.concat(shapes.filter((s) => !keys.has(s.orig + s.dest)));
  }
  cg.setAutoShapes(shapes);
  cg.redrawAll();
  el('ex-back').disabled = histIdx <= 0;
  el('ex-fwd').disabled = histIdx >= history.length - 1;
}

/* Reveal without touching the engine: the answer arrow and the position's
   evaluation are both already known. On a phone a search costs ~0.5-1s and real
   battery, so it waits until there is actually something to explore. */

/* Mover-perspective win% and eval string for a revealed puzzle. Playing an
   accepted alternative gives its own numbers; otherwise the best move's stand. */
function revealedEval(r) {
  if (!r) return { text: '—', win: null };
  const win = r.your_move_win_pct != null ? r.your_move_win_pct : r.win_best;
  return { text: r.eval_yours || r.eval_best || '—', win };
}

function setBarFromMover(win) {
  setEvalBar(win == null ? 50 : (puzzle.color === 'white' ? win : 100 - win));
}

/* The puzzle position, with the green/red arrows: the "here is the answer" view. */
function renderBakedPuzzlePos(r) {
  // Always the best move's evaluation here — this is the pre-move position.
  el('ex-eval').textContent = r ? (r.eval_best || '—') : '—';
  setBarFromMover(r ? r.win_best : null);
  cg.set({
    fen: puzzle.fen,
    turnColor: puzzle.color,
    check: false,
    lastMove: undefined,
    movable: {
      free: false,
      color: puzzle.color,
      dests: new Map(Object.entries(puzzle.dests)),
      events: { after: onMove },
    },
  });
  cg.setAutoShapes(puzzleArrows());
  cg.redrawAll();
  el('ex-back').disabled = histIdx <= 0;
  el('ex-fwd').disabled = histIdx >= history.length - 1;
}

/* A position reached by playing a move, rendered from local rules alone. */
function renderQuietPos(entry) {
  const ev = revealedEval(entry.baked);
  el('ex-eval').textContent = ev.text;
  setBarFromMover(ev.win);
  cg.set({
    fen: entry.pos.fen,
    turnColor: entry.pos.turn,
    check: false,
    lastMove: entry.lastMove,
    movable: {
      free: false,
      color: entry.pos.turn,
      dests: new Map(Object.entries(entry.pos.dests)),
      events: { after: onMove },
    },
  });
  cg.setAutoShapes([]);
  cg.redrawAll();
  el('ex-back').disabled = histIdx <= 0;
  el('ex-fwd').disabled = histIdx >= history.length - 1;
}

async function enterExplore(solvedUci = null, revealed = null) {
  exploring = true;
  el('board-nav').classList.add('active');
  // history[0] is always the puzzle position (rendered with the arrows);
  // after a solve we continue from the position AFTER the winning move.
  history = [{ fen: puzzle.fen, data: null, isPuzzlePos: true, baked: revealed }];
  histIdx = 0;
  if (api.deferEngine) {
    // Leave a solved puzzle looking solved. Snapping the piece back to where it
    // started reads as a rejected move, which is the opposite of what happened.
    if (solvedUci) {
      const pos = await api.positionAfter(puzzle.fen, solvedUci);
      if (pos) {
        history.push({
          fen: pos.fen, data: null, pos, baked: revealed,
          lastMove: [solvedUci.slice(0, 2), solvedUci.slice(2, 4)],
        });
        histIdx = 1;
        renderQuietPos(history[1]);
        return;
      }
    }
    renderBakedPuzzlePos(revealed);
    return;
  }
  if (solvedUci) {
    const data = await evalRequest({ fen: puzzle.fen, move_uci: solvedUci });
    if (data) {
      history.push({ fen: data.fen, data });
      histIdx = 1;
      renderExplore(data);
      return;
    }
  }
  await navTo(0, true);
}

async function exploreMove(uci) {
  const data = await evalRequest({ fen: history[histIdx].fen, move_uci: uci });
  if (!data) { await navTo(histIdx, true); return; }
  history = history.slice(0, histIdx + 1);
  history.push({ fen: data.fen, data });
  histIdx = history.length - 1;
  renderExplore(data);
}

async function navTo(idx, force = false) {
  if (idx < 0 || idx >= history.length) return;
  if (!force && idx === histIdx) return;
  histIdx = idx;
  const entry = history[idx];
  if (!entry.data) {
    // With the engine deferred, the two positions the reveal produced are
    // already fully described — navigating between them costs nothing.
    if (api.deferEngine && entry.isPuzzlePos) { renderBakedPuzzlePos(entry.baked); return; }
    if (api.deferEngine && entry.pos) { renderQuietPos(entry); return; }
    entry.data = await evalRequest({ fen: entry.fen });
    if (!entry.data) return;
  }
  renderExplore(entry.data, { arrows: entry.isPuzzlePos ? puzzleArrows() : null });
}

el('ex-back').addEventListener('click', () => navTo(histIdx - 1));
el('ex-fwd').addEventListener('click', () => navTo(histIdx + 1));
el('ex-reset').addEventListener('click', () => { if (puzzle) navTo(0, true); });

/* ---------- controls ---------- */

/* Forward through the trail if there is one — you got here with "Previous", so
   the puzzle ahead is the one you left, not a new draw. Only past the end does
   this ask for another puzzle. */
function goNext() {
  if (!pendingEmpty && seenIdx < seen.length - 1) { showEntry(seen[++seenIdx]); return; }
  loadPuzzle();
}

function goPrev() {
  // The empty state is not in the trail, so stepping back returns to the last
  // puzzle actually shown rather than skipping over it.
  if (pendingEmpty) { if (seen.length) showEntry(seen[seenIdx]); return; }
  if (seenIdx > 0) showEntry(seen[--seenIdx]);
}

el('giveup').addEventListener('click', () => {
  if (puzzle && !solvedOrRevealed) submit(null);
});
el('next').addEventListener('click', goNext);
el('prev').addEventListener('click', goPrev);
el('discard').addEventListener('click', async () => {
  if (!puzzle) return;
  await api.postDiscard(puzzle.pid);
  seen.splice(seenIdx, 1);   // gone forever: it must not come back via Previous
  if (seenIdx < seen.length) showEntry(seen[seenIdx]);
  else { seenIdx = seen.length - 1; loadPuzzle(); }
});
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key.toLowerCase() === 'n') goNext();
  else if (e.key.toLowerCase() === 'p') goPrev();
  else if (e.key === 'ArrowLeft' && exploring) navTo(histIdx - 1);
  else if (e.key === 'ArrowRight' && exploring) navTo(histIdx + 1);
});
document.querySelectorAll('#filters input').forEach((i) =>
  i.addEventListener('change', () => {
    if (!solvedOrRevealed && !exploring) loadPuzzle({ replace: true });
  }));

applyUrlFilters();
renderFocusPill();
renderSession();
renderNav();
loadPuzzle();
