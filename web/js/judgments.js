/* Port of chess_trainer/judgments.py — the win% model and eval formatting.

   Kept identical to the Python so a puzzle judged on the phone gets the same
   verdict it would get on the laptop. Colors are 'w'/'b' here (chess.js) where
   the Python uses chess.WHITE/BLACK. */

export const WIN_K = 0.00368208;
export const MATE_CP = 10000;

/** Collapse a (cp, mate) score to a single white-relative centipawn number. */
export function scoreToCp(scoreCp, scoreMate) {
  if (scoreMate !== null && scoreMate !== undefined) {
    return scoreMate > 0 ? MATE_CP : -MATE_CP;
  }
  return scoreCp || 0;
}

/** Winning chances (0-100) for `pov` given a white-relative cp score. */
export function winPct(cpWhite, pov) {
  const cp = pov === 'w' ? cpWhite : -cpWhite;
  return 50 + 50 * (2 / (1 + Math.exp(-WIN_K * cp)) - 1);
}

/** Human eval string from `pov`'s perspective: '+2.3', '-0.6', '#4', '-#3'. */
export function fmtEval(scoreCp, scoreMate, pov) {
  if (scoreMate !== null && scoreMate !== undefined) {
    const mate = pov === 'w' ? scoreMate : -scoreMate;
    return mate > 0 ? `#${mate}` : `-#${Math.abs(mate)}`;
  }
  const cp = pov === 'w' ? (scoreCp || 0) : -(scoreCp || 0);
  const pawns = cp / 100;
  return (pawns >= 0 ? '+' : '') + pawns.toFixed(1);
}
