"""Stockfish wrapper via python-chess UCI."""

import contextlib
from dataclasses import dataclass

import chess
import chess.engine

from . import load_config, stockfish_path


@dataclass
class Evaluation:
    score_cp: int | None    # white-relative centipawns (None if mate)
    score_mate: int | None  # white-relative mate distance (None if cp)
    best_uci: str | None
    pv_uci: list[str]


def create(config: dict | None = None) -> chess.engine.SimpleEngine:
    """Start a configured Stockfish process; caller is responsible for .quit()."""
    config = config or load_config()
    engine = chess.engine.SimpleEngine.popen_uci(str(stockfish_path(config)))
    engine.configure({
        "Threads": config["stockfish"]["threads"],
        "Hash": config["stockfish"]["hash_mb"],
    })
    return engine


@contextlib.contextmanager
def open_engine(config: dict | None = None):
    engine = create(config)
    try:
        yield engine
    finally:
        engine.quit()


def evaluate(engine: chess.engine.SimpleEngine, board: chess.Board, nodes: int) -> Evaluation:
    """Evaluate a position. Terminal positions are scored without the engine."""
    if board.is_game_over():
        outcome = board.outcome()
        if outcome and outcome.winner is not None:
            return Evaluation(None, 1 if outcome.winner == chess.WHITE else -1, None, [])
        return Evaluation(0, None, None, [])

    info = engine.analyse(board, chess.engine.Limit(nodes=nodes))
    score = info["score"].white()
    pv = [m.uci() for m in info.get("pv", [])]
    return Evaluation(
        score_cp=score.score(),
        score_mate=score.mate(),
        best_uci=pv[0] if pv else None,
        pv_uci=pv,
    )


def evaluate_multi(engine: chess.engine.SimpleEngine, board: chess.Board,
                   nodes: int, multipv: int = 2) -> list[Evaluation]:
    """Top-N engine lines for a position (empty list for finished games)."""
    if board.is_game_over():
        return []
    infos = engine.analyse(board, chess.engine.Limit(nodes=nodes), multipv=multipv)
    out = []
    for info in infos:
        score = info["score"].white()
        pv = [m.uci() for m in info.get("pv", [])]
        out.append(Evaluation(score.score(), score.mate(), pv[0] if pv else None, pv))
    return out
