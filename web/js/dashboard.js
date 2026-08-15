/* Dashboard: headline stats, the training reset, and (static build) backups. */

import * as api from './api.js';

const el = (id) => document.getElementById(id);

async function load() {
  const s = await api.getStats();
  el('user').textContent = s.username;
  const m = s.mistakes;
  const bank = (m.blunder || 0) + (m.mistake || 0) + (m.inaccuracy || 0);
  const pct = s.games.total ? Math.round(100 * s.games.analyzed / s.games.total) : 0;
  el('tagline').textContent =
    `${s.games.total} games studied · ${bank} puzzles extracted · ${s.due} ready to train`;

  el('cards').innerHTML = `
    <div class="card">
      <div class="label">Games analyzed</div>
      <div class="big">${s.games.analyzed}<span class="frac"> / ${s.games.total}</span></div>
      <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div class="detail">${s.games.pending ? s.games.pending + ' pending — puzzles appear as analysis completes' : 'all games analyzed'}</div>
    </div>
    <div class="card">
      <div class="label">Puzzle bank</div>
      <div class="big">${bank}</div>
      <div class="detail">
        <span class="badge blunder">${m.blunder || 0} blunders</span>
        <span class="badge mistake">${m.mistake || 0} mistakes</span>
        <span class="badge inaccuracy">${m.inaccuracy || 0} inaccuracies</span>
      </div>
    </div>
    <div class="card">
      <div class="label">Due for training</div>
      <div class="big">${s.due}</div>
      <div class="detail"><b>${s.new}</b>&nbsp;never seen yet</div>
    </div>
    <div class="card">
      <div class="label">Attempts so far</div>
      <div class="big">${s.attempts}</div>
      <div class="detail">failed puzzles return within minutes</div>
    </div>`;
}

/* Reset training — two-step, because it cannot be undone. */
const RESET_IDLE = 'Clears every attempt and review schedule. Your puzzles, '
  + 'games and explanations are kept.';
const RESET_ARMED = 'This deletes every attempt and review schedule permanently. '
  + 'Puzzles are kept.';

function showConfirm(on) {
  el('reset-btn').style.display = on ? 'none' : '';
  el('reset-yes').style.display = on ? '' : 'none';
  el('reset-no').style.display = on ? '' : 'none';
  el('reset-note').textContent = on ? RESET_ARMED : RESET_IDLE;
}
el('reset-btn').addEventListener('click', () => showConfirm(true));
el('reset-no').addEventListener('click', () => showConfirm(false));
el('reset-yes').addEventListener('click', async () => {
  el('reset-yes').disabled = true;
  let note;
  try {
    const r = await api.resetTraining();
    note = `Training history cleared — ${r.attempts_deleted} attempt(s) removed. `
         + 'Every puzzle is due again.';
  } catch {
    note = 'Reset failed — is the server running?';
  }
  el('reset-yes').disabled = false;
  showConfirm(false);
  el('reset-note').textContent = note;
  load();
});

if (api.backupSupported) {
  const { mountBackupUi } = await import('./backup.js');
  await mountBackupUi(load);
}

load();
// The server build's numbers move while `analyze` runs; the static build's
// only change when you train, and load() already runs after every action.
if (!api.backupSupported) setInterval(load, 15000);
