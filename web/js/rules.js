/* Chess rules for the static build — ports of the python-chess helpers in
   chess_trainer/web.py and analysis.py, backed by chess.js. */

import { Chess } from '../vendor/chessjs/chess.js';

export { Chess };

/** Port of web.py::_dests — legal destinations keyed by origin square. */
export function dests(chess) {
  const out = {};
  for (const m of chess.moves({ verbose: true })) {
    (out[m.from] ||= []).push(m.to);
    if (m.isKingsideCastle() || m.isQueensideCastle()) {
      // Lichess-style gesture: also allow dropping the king on the rook.
      out[m.from].push((m.isKingsideCastle() ? 'h' : 'a') + m.from[1]);
    }
  }
  return out;
}

/** Port of web.py::_parse_move — maps the king-onto-rook gesture to the real move.
    Returns a chess.js verbose move, or null when the UCI is not playable. */
export function parseMove(chess, uci) {
  const from = uci.slice(0, 2);
  const to = uci.slice(2, 4);
  const promotion = uci.slice(4, 5) || undefined;
  const legal = chess.moves({ verbose: true });

  const direct = legal.find(
    (m) => m.from === from && m.to === to &&
           (promotion ? m.promotion === promotion : !m.promotion));
  if (direct) return direct;

  // King dropped on its own rook: resolve to the castling move.
  const piece = chess.get(from);
  const target = chess.get(to);
  if (piece?.type === 'k' && target?.type === 'r' && target.color === piece.color) {
    const file = to.charCodeAt(0) > from.charCodeAt(0) ? 'g' : 'c';
    return legal.find((m) => m.from === from && m.to === file + from[1]) || null;
  }
  return null;
}

/** Play a verbose move returned by parseMove. */
export function play(chess, move) {
  return chess.move({ from: move.from, to: move.to, promotion: move.promotion });
}

/** Port of analysis.py::_san_line — a UCI principal variation rendered in SAN. */
export function sanLine(fen, pvUci, maxPlies = 12) {
  const chess = new Chess(fen);
  const out = [];
  for (const uci of pvUci.slice(0, maxPlies)) {
    const move = parseMove(chess, uci);
    if (!move) break;
    out.push(play(chess, move).san);
  }
  return out.join(' ');
}

/** Port of insights.py::classify_endgame — pure counting over the FEN piece field. */
export function classifyEndgame(fen) {
  const field = fen.split(' ')[0];
  const pieces = new Set(
    [...field].filter((c) => 'NBRQnbrq'.includes(c)).map((c) => c.toUpperCase()));
  if (pieces.size === 0) return 'pawn';
  if (pieces.has('Q')) return pieces.size === 1 ? 'queen' : 'queen + pieces';
  if (pieces.has('R')) return pieces.size === 1 ? 'rook' : 'rook + minor';
  if (pieces.size === 1 && pieces.has('B')) return 'bishop';
  if (pieces.size === 1 && pieces.has('N')) return 'knight';
  return 'knight + bishop';
}

/** Terminal-position description matching web.py::eval_position's `game_over`. */
export function gameOver(chess) {
  if (chess.isCheckmate()) {
    const winner = chess.turn() === 'w' ? 'black' : 'white';
    return { text: `checkmate — ${winner} wins`, winner };
  }
  if (chess.isStalemate() || chess.isInsufficientMaterial() ||
      chess.isThreefoldRepetition() || chess.isDraw()) {
    return { text: 'draw', winner: null };
  }
  return null;
}
