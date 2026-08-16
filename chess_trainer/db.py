"""SQLite storage for games, evaluations, mistakes and training state."""

import sqlite3
import time
import uuid

from . import DATA_DIR

DB_PATH = DATA_DIR / "trainer.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              TEXT PRIMARY KEY,
    played_at       INTEGER NOT NULL,        -- ms epoch (Lichess createdAt)
    speed           TEXT,
    perf            TEXT,
    rated           INTEGER,
    variant         TEXT,
    color           TEXT,                    -- user's color: white/black
    opponent        TEXT,
    opponent_rating INTEGER,
    user_rating     INTEGER,
    result          TEXT,                    -- win/loss/draw
    status          TEXT,                    -- mate/resign/outoftime/...
    opening         TEXT,
    moves           TEXT,                    -- SAN moves, space separated
    division_middle INTEGER,                 -- ply where middlegame starts
    division_end    INTEGER,                 -- ply where endgame starts
    raw             TEXT,                    -- full NDJSON line
    analyzed_at     INTEGER                  -- local analysis timestamp (s), NULL = pending
);

CREATE TABLE IF NOT EXISTS evals (
    game_id     TEXT NOT NULL,
    ply         INTEGER NOT NULL,            -- half-moves played before this position (0 = start)
    fen         TEXT NOT NULL,
    score_cp    INTEGER,                     -- white-relative centipawns (NULL if mate)
    score_mate  INTEGER,                     -- white-relative mate distance (NULL if cp)
    best_uci    TEXT,
    pv          TEXT,                        -- UCI moves, space separated
    PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS mistakes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id      TEXT NOT NULL,
    ply          INTEGER NOT NULL,           -- position index before the user's move
    fen          TEXT NOT NULL,
    color        TEXT NOT NULL,              -- side to move (the user)
    played_san   TEXT NOT NULL,
    played_uci   TEXT NOT NULL,
    best_san     TEXT NOT NULL,
    best_uci     TEXT NOT NULL,
    pv_san       TEXT,                       -- best line in SAN, space separated
    win_before   REAL NOT NULL,              -- user's win% with best play
    win_after    REAL NOT NULL,              -- user's win% after the played move
    win_loss     REAL NOT NULL,
    judgment     TEXT NOT NULL,              -- inaccuracy/mistake/blunder
    phase        TEXT,                       -- opening/middlegame/endgame
    explanation  TEXT,                       -- the idea behind the best move
    explanation_played TEXT,                 -- what the played move allowed
    explanation_source TEXT,                 -- rules | claude
    motif        TEXT,                       -- explain.MOTIFS category (NULL = positional)
    created_at   INTEGER NOT NULL,
    discarded_at INTEGER,
    UNIQUE (game_id, ply)
);

CREATE TABLE IF NOT EXISTS attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mistake_id   INTEGER NOT NULL REFERENCES mistakes(id),
    attempted_at INTEGER NOT NULL,
    move_uci     TEXT,                       -- NULL = gave up
    correct      INTEGER NOT NULL,
    took_ms      INTEGER,
    sync_id      TEXT                        -- "<device>:<uuid>"; identity across devices
);

CREATE TABLE IF NOT EXISTS scheduling (
    mistake_id    INTEGER PRIMARY KEY REFERENCES mistakes(id),
    ease          REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    due_at        INTEGER NOT NULL DEFAULT 0, -- s epoch; 0 = due now
    reps          INTEGER NOT NULL DEFAULT 0,
    lapses        INTEGER NOT NULL DEFAULT 0,
    last_at       INTEGER,                    -- last review; the sync merge's LWW key
    h             TEXT                        -- puzzle content hash the schedule refers to
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Moves the trainer accepts as alternatives to the engine's first choice,
-- precomputed so the static build can judge answers without an engine.
-- Keyed by (game_id, ply) rather than mistakes.id: re-analysis rebuilds the
-- mistakes table, and this cache must survive that.
CREATE TABLE IF NOT EXISTS accept_sets (
    game_id     TEXT NOT NULL,
    ply         INTEGER NOT NULL,
    fen         TEXT NOT NULL,               -- guards against a rebuilt puzzle at the same key
    nodes       INTEGER NOT NULL,
    multipv     INTEGER NOT NULL,
    moves_json  TEXT NOT NULL,               -- [[uci, cp, mate], ...] mover-POV, best first
    complete    INTEGER NOT NULL,            -- 1 = list provably covers every acceptable move
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (game_id, ply)
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mistakes)")]
    if "explanation_played" not in cols:
        conn.execute("ALTER TABLE mistakes ADD COLUMN explanation_played TEXT")
        conn.commit()
    if "motif" not in cols:
        conn.execute("ALTER TABLE mistakes ADD COLUMN motif TEXT")
        conn.commit()

    # Cross-device sync (sync_state.py) needs two things the local-only schema
    # never did: an identity for each attempt that survives leaving this
    # database, and a review timestamp to break ties against other devices.
    if "sync_id" not in [r[1] for r in conn.execute("PRAGMA table_info(attempts)")]:
        conn.execute("ALTER TABLE attempts ADD COLUMN sync_id TEXT")
        conn.commit()
    # UNIQUE on a nullable column still allows many NULLs in SQLite, which is
    # what pre-sync rows need — they are backfilled on the first sync.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS attempts_sync_id ON attempts(sync_id)")
    sched_cols = [r[1] for r in conn.execute("PRAGMA table_info(scheduling)")]
    if "last_at" not in sched_cols:
        conn.execute("ALTER TABLE scheduling ADD COLUMN last_at INTEGER")
        conn.commit()
    # Carried, not recomputed: `h` says which version of the puzzle a schedule
    # was earned against, and the authority on that is the device that did the
    # reviewing. If the laptop recomputed it from a re-analyzed mistakes row,
    # every phone card would reset the moment the laptop synced — before the
    # rebuilt puzzle had even been deployed.
    if "h" not in sched_cols:
        conn.execute("ALTER TABLE scheduling ADD COLUMN h TEXT")
        conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def now_s() -> int:
    return int(time.time())


def device_id(conn: sqlite3.Connection) -> str:
    """Stable id for this machine, minted on first use.

    It prefixes the sync_id of every attempt recorded here, which is how
    sync_state.py partitions the append-only log between devices: each one
    publishes its own rows and nobody else's.
    """
    value = get_meta(conn, "device_id")
    if not value:
        value = uuid.uuid4().hex[:8]
        set_meta(conn, "device_id", value)
        set_meta(conn, "device_label", "laptop")
        conn.commit()
    return value


def new_sync_id(conn: sqlite3.Connection) -> str:
    return f"{device_id(conn)}:{uuid.uuid4()}"
