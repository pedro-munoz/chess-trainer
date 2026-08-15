"""Personal chess trainer built on locally analyzed Lichess games."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def load_config() -> dict:
    with open(PROJECT_ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def stockfish_path(config: dict) -> Path:
    p = Path(config["stockfish"]["path"])
    return p if p.is_absolute() else PROJECT_ROOT / p
