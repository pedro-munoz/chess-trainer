/* Insights page: strengths/weaknesses stats + Claude-authored coach report. */

import * as api from './api.js';

const el = (id) => document.getElementById(id);
const fmtDate = (ms) => new Date(ms).toISOString().slice(0, 10);

const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/* Tiny markdown renderer for the coach narrative: headings, bold/italic,
   code spans and bullet lists only. */
function mdToHtml(md) {
  const inline = (s) => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  let html = '';
  let inList = false;
  const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
  for (const line of md.split(/\r?\n/)) {
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      closeList();
      const level = Math.min(4, h[1].length + 2);
      html += `<h${level}>${inline(h[2])}</h${level}>`;
    } else if (/^[-*]\s/.test(line)) {
      if (!inList) { html += '<ul>'; inList = true; }
      html += `<li>${inline(line.slice(2))}</li>`;
    } else if (line.trim() === '') {
      closeList();
    } else {
      closeList();
      html += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  return html;
}

function rateBar(value, max, cls = '') {
  const w = max > 0 ? Math.min(100, 100 * value / max) : 0;
  return `<div class="rate-bar"><span class="num">${value.toFixed(1)}</span>` +
    `<div class="track"><div class="fill ${cls}" style="width:${w}%"></div></div></div>`;
}

function trainLink(training) {
  if (!training || !training.available) return '';
  return `<a class="train-link" href="${training.url}" title="${esc(training.label)}">` +
    `train (${training.available}) &rarr;</a>`;
}

function renderReport(data) {
  const report = data.report;
  const banner = el('report-banner');
  if (!report) {
    banner.innerHTML = '<div class="report-banner">No coach report yet — ask Claude Code to '
      + '<b>&ldquo;generate my insights report&rdquo;</b>. The numbers below are live either way.</div>';
    return;
  }
  if (data.report_stale) {
    const base = report.based_on || {};
    banner.innerHTML = `<div class="report-banner">This report was written from `
      + `${base.games ?? '?'} games / ${base.mistakes ?? '?'} mistakes; you now have `
      + `${data.sample.games} / ${data.sample.mistakes}. Ask Claude Code to refresh it.</div>`;
  }
  el('report-head').style.display = '';
  el('verdicts').innerHTML = (report.verdicts || []).map((v) => `
    <div class="card ${v.kind}">
      <div class="label">${esc(v.kind)}</div>
      <h3 class="headline">${esc(v.headline)}</h3>
      <p class="verdict-detail">${esc(v.detail)}</p>
      <div class="detail">
        ${v.confidence ? `<span class="badge phase">${esc(v.confidence)} confidence</span>` : ''}
        ${v.training && v.training.available
          ? `<span>${v.training.available} puzzles ready</span>` : ''}
      </div>
      ${v.training ? `<a class="btn gold verdict-btn" href="${v.training.url}">${esc(v.training.label)} &rarr;</a>` : ''}
    </div>`).join('');
  if (report.narrative_md) {
    el('narrative').innerHTML =
      `<div class="narrative">${mdToHtml(report.narrative_md)}</div>`;
  }
  if (report.generated_at) {
    el('narrative').innerHTML +=
      `<p class="report-meta">Report generated ${fmtDate(report.generated_at * 1000)}`
      + ` · based on ${(report.based_on || {}).games ?? '?'} games</p>`;
  }
}

function renderPhaseColor(data) {
  const rows = data.sections.phase_color;
  const overall = data.overall;
  const max = Math.max(...rows.map((r) => r.per100), overall.per100);
  const get = (phase, color) => rows.find((r) => r.phase === phase && r.color === color);
  el('phase-color').innerHTML =
    '<thead><tr><th>Phase</th><th>As White</th><th>As Black</th></tr></thead><tbody>'
    + ['opening', 'middlegame', 'endgame'].map((phase) => {
      const cells = ['white', 'black'].map((color) => {
        const b = get(phase, color);
        if (!b) return '<td class="dim">—</td>';
        const cls = b.per100 > overall.per100 * 1.15 ? 'bad'
          : b.per100 < overall.per100 * 0.85 ? 'good' : '';
        return `<td>${rateBar(b.per100, max, cls)}` +
          `<div class="cell-note">${b.n_mistakes} mistakes in ${b.n_moves} moves · ` +
          `${b.avg_pawn_loss} pawns lost/move</div></td>`;
      });
      return `<tr><td class="phase">${phase}</td>${cells.join('')}</tr>`;
    }).join('')
    + `</tbody><tfoot><tr><td class="phase">overall</td><td colspan="2">`
    + `${rateBar(overall.per100, max)}<div class="cell-note">${overall.n_mistakes} mistakes `
    + `in ${overall.n_moves} moves</div></td></tr></tfoot>`;
}

function renderEndgames(data) {
  const rows = data.sections.endgames;
  const overall = data.overall;
  const max = Math.max(...rows.map((r) => r.per100), 1);
  el('endgames').innerHTML =
    '<thead><tr><th>Endgame</th><th>Moves</th><th>Mistakes / 100 moves</th>'
    + '<th>Pawns lost / move</th><th></th></tr></thead><tbody>'
    + rows.map((r) => {
      const cls = r.per100 > overall.per100 * 1.15 ? 'bad'
        : r.per100 < overall.per100 * 0.85 ? 'good' : '';
      const dim = r.n_moves < 250 ? ' class="dim-row" title="small sample"' : '';
      return `<tr${dim}><td class="phase">${esc(r.type)}</td>`
        + `<td class="num">${r.n_moves}</td>`
        + `<td>${rateBar(r.per100, max, cls)}</td>`
        + `<td class="num">${r.avg_pawn_loss}</td>`
        + `<td>${trainLink(r.training)}</td></tr>`;
    }).join('') + '</tbody>';
}

function renderOpenings(data) {
  const rows = data.sections.openings;
  const max = Math.max(...rows.map((r) => r.per100), 1);
  el('openings').innerHTML =
    '<thead><tr><th>Opening</th><th>Games</th><th>Score</th>'
    + '<th>Mistakes / 100 moves</th><th></th></tr></thead><tbody>'
    + rows.map((r) => {
      const scoreCls = r.score_pct >= 55 ? 'good-text' : r.score_pct <= 45 ? 'bad-text' : '';
      const dim = r.games < 8 ? ' class="dim-row" title="small sample"' : '';
      return `<tr${dim}><td>${esc(r.family)} <span class="badge phase">${r.color}</span></td>`
        + `<td class="num">${r.games}</td>`
        + `<td class="num ${scoreCls}">${r.score_pct}%</td>`
        + `<td>${rateBar(r.per100, max)}</td>`
        + `<td>${trainLink(r.training)}</td></tr>`;
    }).join('') + '</tbody>';
}

function renderMotifs(data) {
  const rows = data.sections.motifs;
  const max = Math.max(...rows.map((r) => r.share), 1);
  el('motifs').innerHTML =
    '<thead><tr><th>Motif</th><th>Mistakes</th><th>Share</th>'
    + '<th>Pawns lost / mistake</th><th></th></tr></thead><tbody>'
    + rows.map((r) => `<tr><td class="phase">${esc(r.motif.replace(/_/g, ' '))}</td>`
      + `<td class="num">${r.n}</td>`
      + `<td>${rateBar(r.share, max)}</td>`
      + `<td class="num">${r.avg_pawn_loss}</td>`
      + `<td>${trainLink(r.training)}</td></tr>`).join('')
    + '</tbody>';
}

function renderTrend(data) {
  const rows = data.sections.trend;
  if (rows.length < 2) {
    el('trend').innerHTML = '<span class="empty">Not enough months yet.</span>';
    return;
  }
  const W = 700; const H = 170;
  const padL = 34; const padR = 14; const padT = 14; const padB = 34;
  const maxY = Math.max(...rows.map((r) => r.per100)) * 1.15;
  const x = (i) => padL + (W - padL - padR) * (rows.length === 1 ? 0.5 : i / (rows.length - 1));
  const y = (v) => padT + (H - padT - padB) * (1 - v / maxY);
  const pts = rows.map((r, i) => `${x(i).toFixed(1)},${y(r.per100).toFixed(1)}`);
  const grid = [0, 5, 10, 15, 20].filter((g) => g <= maxY);
  el('trend').innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Mistakes per 100 moves by month">
      ${grid.map((g) => `<line x1="${padL}" y1="${y(g)}" x2="${W - padR}" y2="${y(g)}"
          stroke="var(--line)" stroke-width="1"/>
        <text x="${padL - 6}" y="${y(g) + 4}" text-anchor="end" class="axis">${g}</text>`).join('')}
      <polyline points="${pts.join(' ')}" fill="none" stroke="var(--gold)" stroke-width="2.5"
        stroke-linejoin="round" stroke-linecap="round"/>
      ${rows.map((r, i) => `<circle cx="${x(i)}" cy="${y(r.per100)}" r="3.5" fill="var(--gold)">
          <title>${r.month}: ${r.per100}/100 over ${r.games} games (avg opp ${r.avg_opponent_rating})</title>
        </circle>
        <text x="${x(i)}" y="${H - padB + 16}" text-anchor="middle" class="axis">${r.month.slice(2)}</text>
        <text x="${x(i)}" y="${H - padB + 30}" text-anchor="middle" class="axis faint">${r.games}g · ${r.avg_opponent_rating}</text>`).join('')}
    </svg>
    <div class="cell-note">Gold: mistakes per 100 moves. Below each month: games played · average opponent rating.</div>`;
}

function renderSituations(data) {
  const bands = data.sections.bands;
  const misc = data.sections.misc;
  const gap = data.sections.gap;
  const worst = Math.max(...bands.map((b) => b.per100));
  const bandCards = bands.map((b) => `
    <div class="card${b.per100 === worst ? ' weakness' : ''}">
      <div class="label">Moves ${b.band}</div>
      <div class="big">${b.per100.toFixed(1)}<span class="frac"> /100</span></div>
      <div class="detail">${b.n_mistakes} mistakes in ${b.n_moves} moves</div>
    </div>`);
  const gapCards = gap.map((g) => `
    <div class="card">
      <div class="label">vs ${g.gap} opponents</div>
      <div class="big">${g.score_pct !== null ? g.score_pct + '%' : '—'}</div>
      <div class="detail">score over ${g.games} games · ${g.per100.toFixed(1)} mistakes/100</div>
    </div>`);
  const forfeit = misc.forfeit_pct !== null ? `
    <div class="card${misc.forfeit_pct >= 20 ? ' weakness' : ''}">
      <div class="label">Losses on time</div>
      <div class="big">${misc.time_forfeit_losses}<span class="frac"> / ${misc.losses}</span></div>
      <div class="detail">${misc.forfeit_pct}% of your losses are clock deaths</div>
    </div>` : '';
  el('situations').innerHTML = bandCards.join('') + forfeit + gapCards.join('');
}

function renderSignals(data) {
  el('signals').innerHTML =
    '<thead><tr><th>Dimension</th><th>Bucket</th><th>Rate</th><th>Baseline</th>'
    + '<th>z</th><th>Moves</th><th></th></tr></thead><tbody>'
    + data.signals.map((s) => {
      const strong = Math.abs(s.z) >= 2;
      const cls = !strong ? ' class="dim-row"' : '';
      const zCls = strong ? (s.z > 0 ? 'bad-text' : 'good-text') : '';
      return `<tr${cls}><td class="phase">${esc(s.dimension.replace(/_/g, ' '))}</td>`
        + `<td>${esc(s.key.replace(/\|/g, ' · '))}</td>`
        + `<td class="num">${s.per100.toFixed(1)}</td>`
        + `<td class="num">${s.baseline_per100.toFixed(1)}</td>`
        + `<td class="num ${zCls}">${s.z > 0 ? '+' : ''}${s.z.toFixed(1)}</td>`
        + `<td class="num">${s.n_moves}</td>`
        + `<td>${trainLink(s.training)}</td></tr>`;
    }).join('') + '</tbody>';
}

async function load() {
  const data = await api.getInsights();
  const [from, to] = data.sample.date_range;
  el('tagline').textContent =
    `${data.sample.games} games · ${data.sample.own_moves.toLocaleString()} of your moves analyzed · `
    + `${data.sample.mistakes} mistakes` + (from ? ` · ${fmtDate(from)} to ${fmtDate(to)}` : '');
  renderReport(data);
  renderPhaseColor(data);
  renderEndgames(data);
  renderOpenings(data);
  renderMotifs(data);
  renderTrend(data);
  renderSituations(data);
  renderSignals(data);
}

load();
