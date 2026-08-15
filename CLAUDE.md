# Chess Trainer

Personal chess trainer for Lichess user **Katutx0**. Downloads his games, analyzes
them locally with Stockfish, extracts mistakes as puzzles, and serves a
"Learn from your mistakes" web trainer with Anki-style spaced repetition.
No Lichess credentials are used anywhere; only the public API / manual exports.

## Layout

- `chess_trainer/` — Python package
  - `cli.py` — entry point: `python -m chess_trainer {sync|analyze|serve}`
  - `lichess.py` — game download (API NDJSON stream) + `--file` import (NDJSON or PGN)
  - `db.py` — SQLite schema (`data/trainer.db`): games, evals, mistakes, attempts, scheduling, meta
  - `engine.py` — Stockfish wrapper (python-chess); `create()` for long-lived use, `open_engine()` context manager for batch jobs
  - `analysis.py` — per-game evaluation, mistake extraction (resumable via `games.analyzed_at`)
  - `judgments.py` — Lichess win% formula; thresholds in `config.toml` (10/20/30 win% drop)
  - `explain.py` — rule-based explanations (`explanation_source='rules'`; `'claude'` reserved for future LLM-generated ones)
    + `classify_motif()` → `mistakes.motif` (allowed_mate/missed_mate/hanging_piece/
    missed_fork/allowed_fork/missed_material/positional; NULL reads as positional)
  - `srs.py` — SM-2-lite scheduling (fail → due in 10 min; success → 1d, 3d, then ×ease)
  - `insights.py` — strengths/weaknesses stats: builds per-own-move facts from `evals`
    (denominators!), aggregates rates per color/phase/endgame-type/opening-family/
    motif/move-band/rating-gap/month, emits z-score signals vs the overall rate, and
    serves the Claude-authored coach report from `data/insights_report.json`
    (in-process cache, invalidated by a SQL probe + report-file mtime).
    `classify_endgame(fen)` buckets endgame-phase positions by remaining pieces
    (pawn/rook/queen/bishop/knight/rook + minor/queen + pieces/knight + bishop) —
    endgame analysis is by type, deliberately NOT by color.
    **Cost is reported in pawns, never win%** (`avg_pawn_loss`, via `_pawn_loss()`,
    evals clamped to ±10 pawns so decided positions don't dominate). Pedro asked for
    valuation points on screen; win% survives only *inside* `judgments.judge` as the
    blunder/mistake/inaccuracy classifier, which no UI surfaces
  - `web.py` — FastAPI: `/api/stats`, `/api/next`, `/api/attempt`, `/api/discard`,
    `/api/eval` (free exploration: server-side Stockfish evaluates any FEN/move),
    `/api/insights` (+`?refresh=1`) + static frontend. `/api/next` filters:
    `judgments_filter`, `phases`, `color`, `opening` (family prefix), `motif` (CSV),
    `endgame` (CSV of `classify_endgame` types, filtered in Python), `focus=1`
    (selection weighted by `win_loss + 15*lapses` instead of uniform random).
    `POST /api/reset-training {confirm: true}` wipes practice history via
    `srs.reset_training` (shared with `scripts/reset_training.py`) — exposed as the
    two-step "Reset training" button in the dashboard's danger zone
- `scripts/` — one-off maintenance, run as `python -m scripts.<name>`:
  `fix_phases.py` (recompute phases from FENs), `regen_explanations.py` (rebuild
  pv_san + explanations from stored evals — run after improving `explain.py`),
  `backfill_motifs.py` (recompute `mistakes.motif` — run after improving
  `classify_motif`; no re-analysis needed for either),
  `export_explain_batches.py` / `import_claude_explanations.py` (detailed
  explanations, see below), `reset_training.py --yes` (wipe `attempts` +
  `scheduling` for a fresh start; puzzles/evals/explanations untouched)
- `web/` — frontend, no build step. `index.html` dashboard (hero + summary cards
  only; all breakdowns live on Insights — `/api/stats` still returns `by_phase`
  and `by_color`, now unused), `insights.html`
  strengths/weaknesses + coach report (`js/insights.js`), `train.html` trainer
  (chessground 9.1.1 from jsdelivr — **ES module**, must be loaded via `import`, not `<script src>`).
  `train.html` accepts URL params (`judgments`, `phases`, `color` pre-set the pills;
  `opening`, `motif`, `focus` ride along) so Insights cards can launch filtered sessions
- `config.toml` — username, stockfish path, nodes/thresholds/tolerance, port (8123)
- `tools/stockfish/` — Stockfish 18 AVX2 binary (gitignored)
- `.venv/` — Python 3.14 venv with chess, requests, fastapi, uvicorn

Run everything from the project root with `./.venv/Scripts/python.exe`.

## Daily routine (Pedro plays daily)

```
./.venv/Scripts/python.exe -m chess_trainer sync      # fetch only NEW games (incremental)
./.venv/Scripts/python.exe -m chess_trainer analyze   # analyze pending games -> new puzzles
./.venv/Scripts/python.exe -m chess_trainer serve     # trainer at http://localhost:8123
```

- `sync` is incremental: `meta.last_sync_created_at` is the cursor; only games newer
  than the last import are fetched (`sort=dateAsc&since=`). Committed every 20 games,
  so it resumes cleanly after interruption. Retries 5× with escalating backoff on 429.
- `analyze` picks up games with `analyzed_at IS NULL` (standard variant only), newest
  first, and commits per game — safe to Ctrl+C anytime. ~15–25 s/game at the default
  1,000,000 nodes/position; use `--limit N` / `--nodes 100000` for quick passes.
- The trainer needs no refresh step: `/api/next` reads the mistakes table live, so new
  puzzles appear as soon as their game is analyzed. Never-seen puzzles and due reviews
  are mixed randomly; failed ones come back in ~10 minutes.
- If the API is blocked (see findings), download the export in a logged-in browser from
  https://lichess.org/@/Katutx0/download (PGN or NDJSON both work) and run:
  `./.venv/Scripts/python.exe -m chess_trainer sync --file "C:/path/to/export.pgn"`
  The cursor is updated either way, so later API syncs stay incremental.

The `serve` dev-server is also configured in `.claude/launch.json` (name `trainer`,
autoPort enabled — `serve` honors the `PORT` env var, then `--port`, then config.toml).

## Coach report (Insights page)

The Insights page (`/insights.html`) always shows live stats from `/api/insights`.
The headline verdict cards + narrative come from **`data/insights_report.json`**,
which is written by Claude Code, not generated at runtime. When Pedro asks to
"refresh my insights report" (do it once the sample has grown ~10%; the page shows a
staleness banner): read `/api/insights` (or `insights.compute_insights`) — especially
`signals` (z-scores vs baseline) — drill into games/SQL as needed, and rewrite the
JSON: `{generated_at (s), based_on: {games, mistakes}, verdicts: [{kind:
weakness|strength, headline, detail, confidence, training: {label, url, available}
| null}], narrative_md}`. Ground every claim in the numbers, state sample sizes,
only trust |z| ≥ 2, and keep training URLs in the `/train.html?...&judgments=
blunder,mistake&focus=1` form (get `available` from `insights._available`).
**Voice**: a grandmaster coaching his pupil — direct, personal, prescriptive;
every verdict aims at fixing a weakness or reinforcing a strength (strength cards
get training links too). Endgame claims use types, never endgame-by-color.
**Units**: state cost in pawns ("0.66 pawns per move", "3.5 pawns each") — never
win%, never centipawns. Mistake *rates* per 100 moves still carry the z-scored
evidence; pawn loss is the severity companion, and the two can disagree (endgames
show few flagged mistakes but bleed as much per move as middlegames — say so).

## Detailed explanations (Claude-written)

Puzzle explanations start as rule-based (`explanation_source='rules'`); Claude Code
upgrades them to detailed coaching text (`'claude'`). When Pedro asks to "explain
new puzzles" (e.g. after analyzing new games):

1. `python -m scripts.export_explain_batches` — dumps puzzles with source `'rules'`
   (non-discarded) as JSON batches to `data/explain_work/batches/` with full context
   (FEN, best/punish lines, evals, game continuation).
2. Fan out subagents (one per batch) that write
   `data/explain_work/out/batch_NNN.json` arrays of `{id, explanation,
   explanation_played}` — see the style rules used in the prompt: concrete, coach-like,
   grounded in the provided lines, ≤700 chars per field, python-chess to verify
   uncertain tactics. **Tell each agent to write its output file early and complete,
   then refine** — the first run of this job lost 6 batches to a session limit because
   agents spent 130k+ tokens on Stockfish verification before writing anything. Keep
   concurrency ≤4 for the same reason, and have agents name helper scripts per batch
   (they share one scratchpad). Agents should hedge rather than assert when a line
   looks odd; several "corrupt FEN" reports turned out to be misreadings.
3. `python -m scripts.import_claude_explanations` — validates and writes back,
   setting `explanation_source='claude'`.

`regen_explanations.py` skips `'claude'` rows so reruns never clobber them
(re-analysis of a game still rebuilds its mistakes as `'rules'`, after which the
export/explain/import cycle picks them up again).

## Hard-won findings (2026-08-14)

1. **Lichess bot-masks anonymous API clients.** Generic UAs (python-requests, curl)
   get `404` on `/api/games/user/...`; a descriptive `User-Agent` (see
   `lichess.USER_AGENT`) gets real responses. Keep that header.
2. **"Please only run 1 request(s) at a time"** from the export endpoint means an
   export stream for this IP is *already open somewhere* — it blocked us for 30+ min
   from scripts AND a clean browser, and predated all our requests. The logged-in
   browser export lane is separate and always worked. Retrying aggressively extends
   rate-limit penalties; back off and prefer the manual export fallback.
3. **Requesting Lichess server analysis has no official API** and is capped at
   ~35/day. Out of scope permanently — local Stockfish replaces it 1:1 (same win%
   formula, same judgment thresholds as lila).
4. **uvicorn on Windows installs `WindowsSelectorEventLoopPolicy`**, under which
   python-chess's engine thread cannot spawn subprocesses → `EngineTerminatedError:
   engine event loop dead`. Fix in `web._get_engine`: restore
   `chess.engine.EventLoopPolicy()` before creating the engine.
5. **Never call `open_engine(cfg).__enter__()` without keeping the context manager
   reference** — the GC finalizes the generator and runs its `finally: engine.quit()`,
   silently killing Stockfish. Use `engine.create()` for long-lived engines.
6. **chessground 9.x dist is ESM** (`export { Chessground }`), and its cburnett piece
   CSS inlines data URIs — CDN usage works fully offline-after-cache with no build.
7. `/api/next` deliberately omits `best_uci` so the client can't leak the answer;
   move checking is server-side (exact best move, or live Stockfish check within
   `tolerance_winpct` of the stored best win%).
8. PGN exports lack `division` (game-phase) data — `judgments.phase_of` falls back to
   a ply-based heuristic; NDJSON syncs include it. All 301 current games are
   PGN-imported, so **every stored phase is heuristic**; future NDJSON syncs will mix
   definitions. `insights._phase_from_fen` must mirror `phase_of` exactly (it counts
   `NBRQnbrq` in the FEN instead of building a Board — verified 1060/1060 equal).
9. `games.status` for time losses is `"time forfeit"` (PGN Termination) but would be
   `"outoftime"` from NDJSON — match with `'time' in status.lower()`. Beware: those
   statuses include games the *opponent* lost on time; always AND with `result='loss'`.

## Conventions

- Win%/judgment math mirrors Lichess: `Win% = 50 + 50*(2/(1+exp(-0.00368208*cp))-1)`,
  mate collapsed to ±10000 cp. This stays the *internal* classifier (it correctly
  ignores a 2-pawn swing in an already-won game); everything user-facing is in pawns.
- Evals are stored white-relative; convert to mover perspective via `judgments.win_pct`.
- Discarded puzzles keep their row (`discarded_at` set) so re-analysis never
  resurrects them; re-analysis deletes and rebuilds non-discarded mistakes + their
  attempts/scheduling.
