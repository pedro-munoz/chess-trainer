"""Win-percentage model and move judgments, following Lichess's accuracy model."""

import math

import chess
import chess.engine

# Lichess: Win% = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)
WIN_K = 0.00368208
MATE_CP = 10_000


def score_to_cp(score_cp: int | None, score_mate: int | None) -> int:
    """Collapse a (cp, mate) score to a single centipawn number (white-relative)."""
    if score_mate is not None:
        return MATE_CP if score_mate > 0 else -MATE_CP
    return score_cp or 0


def win_pct(cp_white: int, pov: chess.Color) -> float:
    """Winning chances (0-100) for `pov` given a white-relative cp score."""
    cp = cp_white if pov == chess.WHITE else -cp_white
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-WIN_K * cp)) - 1.0)


def fmt_eval(score_cp: int | None, score_mate: int | None, pov: chess.Color) -> str:
    """Human eval string from `pov`'s perspective: '+2.3', '-0.6', '#4', '-#3'."""
    if score_mate is not None:
        mate = score_mate if pov == chess.WHITE else -score_mate
        return f"#{mate}" if mate > 0 else f"-#{abs(mate)}"
    cp = (score_cp or 0) if pov == chess.WHITE else -(score_cp or 0)
    return f"{cp / 100:+.1f}"


def judge(win_before: float, win_after: float, thresholds: dict) -> str | None:
    """Classify the mover's win% drop. Returns None when the move is fine."""
    loss = win_before - win_after
    if loss >= thresholds["blunder"]:
        return "blunder"
    if loss >= thresholds["mistake"]:
        return "mistake"
    if loss >= thresholds["inaccuracy"]:
        return "inaccuracy"
    return None


def majors_and_minors(board: chess.Board) -> int:
    return sum(
        len(board.pieces(pt, color))
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for color in (chess.WHITE, chess.BLACK)
    )


def phase_of(board: chess.Board, ply: int,
             division_middle: int | None, division_end: int | None) -> str:
    """Game phase for a position, using Lichess's division when available.

    PGN exports carry no division data, so fall back to material on the board
    (Lichess's own divider calls it an endgame at <= 6 majors+minors).
    """
    if division_middle is not None or division_end is not None:
        if division_end is not None and ply >= division_end:
            return "endgame"
        if division_middle is not None and ply >= division_middle:
            return "middlegame"
        return "opening"
    mm = majors_and_minors(board)
    if mm <= 6:
        return "endgame"
    if ply >= 20 or mm <= 10:
        return "middlegame"
    return "opening"
