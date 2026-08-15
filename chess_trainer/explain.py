"""Rule-based explanation of why the engine's best move beats the played move.

v1: derived purely from engine data (PV, evals) plus board inspection with
python-chess. A `claude` explanation source can replace these later.
"""

import chess

PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}
PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        PIECE_VALUES[pt] * len(board.pieces(pt, color))
        for pt in PIECE_VALUES
    )


def _material_swing_along_pv(board: chess.Board, pv_uci: list[str], mover: chess.Color) -> int:
    """Net material change for `mover` after playing out the PV (up to 10 plies)."""
    b = board.copy()
    start = _material(b, mover) - _material(b, not mover)
    for uci in pv_uci[:10]:
        move = chess.Move.from_uci(uci)
        if move not in b.legal_moves:
            break
        b.push(move)
    return (_material(b, mover) - _material(b, not mover)) - start


def _fork_targets(board: chess.Board, move: chess.Move) -> list[str]:
    """Valuable enemy pieces attacked by the moved piece after `move`."""
    b = board.copy()
    b.push(move)
    piece = b.piece_at(move.to_square)
    if piece is None:
        return []
    targets = []
    for sq in b.attacks(move.to_square):
        victim = b.piece_at(sq)
        if victim and victim.color != piece.color:
            if victim.piece_type == chess.KING or PIECE_VALUES[victim.piece_type] > PIECE_VALUES[piece.piece_type] \
               or not b.is_attacked_by(victim.color, sq):
                targets.append(f"{PIECE_NAMES[victim.piece_type]} on {chess.square_name(sq)}")
    return targets


def _hanging_after(board: chess.Board, played: chess.Move) -> str | None:
    """If the played move leaves a piece capturable at a profit, name it."""
    b = board.copy()
    mover = b.turn
    b.push(played)
    best_prey = None
    best_gain = 0
    for sq, piece in b.piece_map().items():
        if piece.color != mover or piece.piece_type == chess.KING:
            continue
        attackers = b.attackers(not mover, sq)
        if not attackers:
            continue
        cheapest = min(
            (b.piece_at(a) for a in attackers),
            key=lambda p: PIECE_VALUES[p.piece_type],
        )
        defended = bool(b.attackers(mover, sq))
        gain = PIECE_VALUES[piece.piece_type] - (PIECE_VALUES[cheapest.piece_type] if defended else 0)
        if not defended:
            gain = PIECE_VALUES[piece.piece_type]
        if gain > best_gain:
            best_gain = gain
            best_prey = f"{PIECE_NAMES[piece.piece_type]} on {chess.square_name(sq)}"
    return best_prey if best_gain >= 2 else None


MOTIFS = ("allowed_mate", "missed_mate", "hanging_piece", "missed_fork",
          "allowed_fork", "missed_material", "positional")


def classify_motif(
    board: chess.Board,
    played: chess.Move,
    best: chess.Move,
    pv_uci: list[str],
    best_mate: int | None,
    played_mate_against: int | None,
    punish_uci: list[str] | None = None,
) -> str:
    """Primary motif of a mistake; first matching category wins."""
    if played_mate_against is not None:
        return "allowed_mate"
    if best_mate is not None:
        return "missed_mate"
    if _hanging_after(board, played):
        return "hanging_piece"
    if len(_fork_targets(board, best)) >= 2:
        return "missed_fork"
    if punish_uci:
        b = board.copy()
        b.push(played)
        reply = chess.Move.from_uci(punish_uci[0])
        if reply in b.legal_moves and len(_fork_targets(b, reply)) >= 2:
            return "allowed_fork"
    if _material_swing_along_pv(board, pv_uci, board.turn) >= 2:
        return "missed_material"
    return "positional"


def explain_parts(
    board: chess.Board,
    played: chess.Move,
    best: chess.Move,
    pv_uci: list[str],
    best_mate: int | None,       # mover-relative mate distance of best line (None if no mate)
    played_mate_against: int | None,  # mate distance against mover after played move
    eval_before: str | None = None,    # mover-perspective eval with best play, e.g. "+2.3"
    eval_after: str | None = None,     # mover-perspective eval after the played move
    punish_san: str | None = None,     # opponent's best continuation after the played move
) -> tuple[str, str]:
    """Explain a puzzle in two separate parts:
    (idea of the best move, what the played move allowed)."""
    mover = board.turn
    best_san = board.san(best)
    played_san = board.san(played)

    # Part 1 — the idea behind the best move.
    idea = None
    if best_mate is not None and best_mate > 0:
        idea = f"{best_san} forces checkmate in {best_mate}."
    else:
        swing = _material_swing_along_pv(board, pv_uci, mover)
        if swing >= 2:
            victim = None
            b2 = board.copy()
            for uci in pv_uci[:6]:
                mv = chess.Move.from_uci(uci)
                if mv not in b2.legal_moves:
                    break
                if b2.turn == mover and b2.is_capture(mv):
                    target = b2.piece_at(mv.to_square)
                    if target and PIECE_VALUES[target.piece_type] >= 3:
                        victim = f"the {PIECE_NAMES[target.piece_type]} on {chess.square_name(mv.to_square)}"
                        break
                b2.push(mv)
            what = f", picking up {victim}" if victim else ""
            idea = f"{best_san} wins material{what} — about {swing} points ahead by the end of the line."
        if idea is None:
            forks = _fork_targets(board, best)
            if len(forks) >= 2:
                idea = f"{best_san} creates a double attack: it hits the {' and the '.join(forks[:2])}."
        if idea is None and board.gives_check(best):
            idea = f"{best_san} gives check and keeps the initiative."
        if idea is None and board.is_capture(best):
            target = board.piece_at(best.to_square)
            if target:
                idea = (f"{best_san} removes the {PIECE_NAMES[target.piece_type]} on "
                        f"{chess.square_name(best.to_square)} at the right moment.")
    if idea is None:
        if eval_before is not None:
            idea = f"{best_san} keeps the evaluation at {eval_before}."
        else:
            idea = f"The engine prefers {best_san} here."

    # Part 2 — what the played move allowed.
    segs = []
    if played_mate_against is not None:
        segs.append(f"allowed checkmate in {played_mate_against}")
    else:
        prey = _hanging_after(board, played)
        if prey:
            segs.append(f"left the {prey} hanging")
    if eval_before is not None and eval_after is not None:
        segs.append(f"dropped the evaluation from {eval_before} to {eval_after}")
    played_text = f"In the game you played {played_san}"
    if segs:
        played_text += ", which " + " and ".join(segs)
    played_text += "."
    if punish_san:
        played_text += f" Punishment: {punish_san}."
    return idea, played_text
