# Chess Trainer

Personal chess trainer for Lichess user **Katutx0**. Downloads his games, analyzes
them locally with Stockfish, extracts mistakes as puzzles, and serves a
"Learn from your mistakes" web trainer with Anki-style spaced repetition.
No Lichess credentials are used anywhere; only the public API / manual exports.

**Pedro trains on his phone, not here.** The laptop is the authoring and analysis
environment; the thing he actually uses is a static build of the same frontend,
published to GitHub Pages and installed as a PWA (see "Static build" below). The
FastAPI server is for developing, analyzing and writing insights — its `attempts`
and `scheduling` tables stay empty because no training happens against it.

## Layout

- `chess_trainer/` — Python package
  - `cli.py` — entry point: `python -m chess_trainer {sync|analyze|serve}`
  - `lichess.py` — game download (API NDJSON stream) + `--file` import (NDJSON or PGN)
  - `db.py` — SQLite schema (`data/trainer.db`): games, evals, mistakes, attempts,
    scheduling, meta, accept_sets
  - `accept.py` — precomputed acceptable-move sets for the static build: for each
    puzzle, runs multipv-10 and re-evaluates each candidate the way `web.attempt`
    does, keeping the ones within `tolerance_winpct` of `win_before`. `complete=1`
    means a candidate was *rejected*, which proves the list covers every acceptable
    move (95.8% of puzzles); otherwise the client falls back to its own engine.
    Cached by `(game_id, ply)` + fen, so re-analysis never invalidates it
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
    two-step "Reset training" button in the dashboard's danger zone.
    `/api/next` also returns `pid`; `/api/attempt` and `/api/discard` accept
    either `mistake_id` or `pid`
- `scripts/` — one-off maintenance, run as `python -m scripts.<name>`:
  `fix_phases.py` (recompute phases from FENs), `regen_explanations.py` (rebuild
  pv_san + explanations from stored evals — run after improving `explain.py`),
  `backfill_motifs.py` (recompute `mistakes.motif` — run after improving
  `classify_motif`; no re-analysis needed for either),
  `export_explain_batches.py` / `import_claude_explanations.py` (detailed
  explanations, see below), `reset_training.py --yes` (wipe `attempts` +
  `scheduling` for a fresh start; puzzles/evals/explanations untouched).
  Static build: `precompute_accept.py`, `export_static.py`, `deploy_pages.py`,
  `serve_dist.py`, `import_phone_state.py`, plus the one-off asset generators
  `vendor_fonts.py` and `make_icons.py`
- `web/` — frontend, no build step, **serves both modes**. `index.html` dashboard
  (hero + summary cards only; all breakdowns live on Insights — `/api/stats` still
  returns `by_phase`/`by_color`, now unused), `insights.html` strengths/weaknesses
  + coach report (`js/insights.js`), `train.html` trainer (chessground 9.1.1,
  **ES module**, must be loaded via `import`, not `<script src>`).
  `train.html` accepts URL params (`judgments`, `phases`, `color` pre-set the pills;
  `opening`, `motif`, `endgame`, `focus` ride along) so Insights cards can launch
  filtered sessions.
  - `js/api.js` picks `api.server.js` (fetch → FastAPI) or `api.local.js`
    (everything in-browser) based on `js/config.js`, which the export rewrites.
    **UI code only ever calls the `api.*` contract** — keep both backends
    returning the identical JSON shapes
  - `js/{data,store,srs,judgments,rules,engine,backup,pwa,dashboard}.js` are
    static-mode support; `srs.js`, `judgments.js` and `rules.js` are ports of the
    Python and must be changed in step with it
  - `web/vendor/` is committed (chessground, chess.js, latin-subset fonts,
    Stockfish wasm) — offline needs it, and it is a build input
- `config.toml` — username, stockfish path, nodes/thresholds/tolerance, port (8123)
- `tools/stockfish/` — Stockfish 18 AVX2 binary (gitignored)
- `.venv/` — Python 3.14 venv with chess, requests, fastapi, uvicorn, pillow

Run everything from the project root with `./.venv/Scripts/python.exe`.

## Daily routine (Pedro plays daily)

```
./.venv/Scripts/python.exe -m chess_trainer sync      # fetch only NEW games (incremental)
./.venv/Scripts/python.exe -m chess_trainer analyze   # analyze pending games -> new puzzles
./.venv/Scripts/python.exe -m scripts.deploy_pages    # publish to the phone
```

`deploy_pages` runs the accept-set precompute, builds `dist/` and force-pushes it
to `gh-pages`. `python -m chess_trainer serve` is still the way to work on the
frontend locally, but it is no longer part of the routine — Pedro trains on the
published build.

- `sync` is incremental: `meta.last_sync_created_at` is the cursor; only games newer
  than the last import are fetched (`sort=dateAsc&since=`). Committed every 20 games,
  so it resumes cleanly after interruption. Retries 5× with escalating backoff on 429.
- `analyze` picks up games with `analyzed_at IS NULL` (standard variant only), newest
  first, and commits per game — safe to Ctrl+C anytime. ~15–25 s/game at the default
  1,000,000 nodes/position; use `--limit N` / `--nodes 100000` for quick passes.
- On the dev server the trainer needs no refresh step: `/api/next` reads the mistakes
  table live. The published build is a snapshot, so new puzzles reach the phone only
  on the next `deploy_pages`. Never-seen puzzles and due reviews are mixed randomly;
  failed ones come back in ~10 minutes.
- New puzzles cost ~1.2 s each in `precompute_accept`; already-computed ones are free.
- If the API is blocked (see findings), download the export in a logged-in browser from
  https://lichess.org/@/Katutx0/download (PGN or NDJSON both work) and run:
  `./.venv/Scripts/python.exe -m chess_trainer sync --file "C:/path/to/export.pgn"`
  The cursor is updated either way, so later API syncs stay incremental.

The `serve` dev-server is also configured in `.claude/launch.json` (name `trainer`,
autoPort enabled — `serve` honors the `PORT` env var, then `--port`, then config.toml).
`trainer-static` (port 8124) serves `dist/` under `/chess-trainer/` via
`scripts/serve_dist.py`, which fixes the `.wasm` MIME type Windows gets wrong and
exercises the subpath layout Pages uses.

## Static build (what Pedro actually uses)

`python -m scripts.export_static` writes `dist/`: the same `web/` frontend with
`js/config.js` rewritten to `MODE='static'`, the vendored assets, a service
worker, and four JSON files under `data/`. Current sizes — `puzzles.json`
911 KB raw / 306 KB gzipped, `games.json` 34 KB, `insights.json` 26 KB, engine
7.0 MB; ~8.5 MB total, ~1.6 MB without the engine.

**The 21k-row `evals` table never ships.** Insights is precomputed, and the one
eval the trainer reads at runtime (the puzzle's own, for `eval_best`) is baked
into each puzzle record as `ev`.

Puzzle records use short keys — `p` pid, `f` fen, `c` color, `n` move number,
`ps`/`pu` played, `bs`/`bu` best, `pv`, `e`/`ep` explanations, `wb` win_before,
`wl` win_loss, `j` judgment, `ph` phase, `mo` motif, `eg` endgame type, `ev`,
`mv` accept set, `mvc` complete flag, `h` content hash.

### Puzzle identity across redeploys — the thing to be careful with

`mistakes.id` is AUTOINCREMENT and re-analysis **deletes and rebuilds** rows, so
it cannot key anything that must survive a rebuild. Everything client-side keys
off **`pid` = `"<game_id>:<ply>"`** (backed by `UNIQUE(game_id, ply)`).

Each puzzle also carries `h = sha1(fen|best_uci|judgment)[:8]`. On load
`api.local.js::reconcile` compares it: unchanged → schedule kept verbatim;
changed → the question is different, so reps/interval/due reset **but `lapses`
and the attempt log survive**; puzzle gone → tombstoned with `orphan_since`,
never deleted, because re-analysis may bring it back.

So: **do not re-analyze already-analyzed games casually.** A different node count
changes some `best_uci` values, which changes `h`, which resets those cards.

### Training state lives only on the phone

IndexedDB `chess-trainer`: stores `srs` (keyed by pid), `attempts`, `discards`,
`meta`. `navigator.storage.persist()` is requested on first load, but "Clear
browsing data" still wipes everything — hence the dashboard's Backup block and
its 14-day nag. `scripts/import_phone_state.py --file <backup.json>` carries
**discards** back into `mistakes.discarded_at`; without that step the next export
ships them again. `--srs` also mirrors the schedule/attempts, which is only worth
it if you want to query the history with SQL.

### Deploying

`python -m scripts.deploy_pages [--dry-run]` builds and force-pushes `dist/` to
`gh-pages`, **amending so the branch stays a single commit** — otherwise a daily
1 MB data file plus a 7 MB engine would grow the repo without bound. The build
cannot run in CI: it needs `data/trainer.db` and the local Stockfish, neither of
which is pushed.

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
only trust |z| ≥ 2, and keep training URLs in the **relative** `train.html?...&
judgments=blunder,mistake&focus=1` form — no leading slash, which would break on
the Pages subpath (`_load_report` strips one if present, but write them right).
Get `available` from `insights._available`.

**"opening" is two different things — do not mix them.** `phases=opening` is the
game phase (~38 puzzles); `opening=Caro-Kann Defense` is an opening *family* and
matches every mistake in those games, at any phase. A verdict about a phase must
use `phases=`; a verdict about a repertoire uses `opening=`. Getting this wrong
is not visible in the UI — the link renders as "train (139)" either way — so
`export_static.check_report_links` warns when a headline says "phase" and the URL
does not filter by one. Phase, phase×color and endgame links are pure by
construction; opening-family links deliberately span all phases.
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
   CSS inlines data URIs — it works fully offline with no build. Now vendored in
   `web/vendor/chessground/` rather than loaded from jsdelivr.
7. `/api/next` deliberately omits `best_uci` so the client can't leak the answer;
   move checking is server-side (exact best move, or live Stockfish check within
   `tolerance_winpct` of the stored best win%). **This is void in the static build** —
   `puzzles.json` necessarily contains `bu`. There is nowhere to hide it on a static
   site and obfuscation would be theatre; it is a single-user personal trainer.
8. PGN exports lack `division` (game-phase) data — `judgments.phase_of` falls back to
   a ply-based heuristic; NDJSON syncs include it. All 301 current games are
   PGN-imported, so **every stored phase is heuristic**; future NDJSON syncs will mix
   definitions. `insights._phase_from_fen` must mirror `phase_of` exactly (it counts
   `NBRQnbrq` in the FEN instead of building a Board — verified 1060/1060 equal).
9. `games.status` for time losses is `"time forfeit"` (PGN Termination) but would be
   `"outoftime"` from NDJSON — match with `'time' in status.lower()`. Beware: those
   statuses include games the *opponent* lost on time; always AND with `result='loss'`.

### Static-build findings (2026-08-15)

10. **`go nodes N` is only deterministic for a fixed search history.** The server's
    long-lived engine has a warm hash table; the precompute pass does not. Verifying
    102 baked verdicts against the live `/api/attempt` gave **96 agreements and 6
    disagreements, all within ~1.5 win% of the tolerance boundary**. Nothing is wrong
    — the desktop is not self-consistent across restarts either. Since Pedro trains
    only on the phone, the baked set is the source of truth and self-consistency is
    what matters.
11. **The best move is not always in its own accept set** (36 of 1060). `win_before`
    comes from the 1,000,000-node analysis pass while acceptance is judged at 400,000
    nodes, so the bar can sit above what the shallower search says the best move is
    worth. **`web.attempt` has exactly the same quirk** (it short-circuits on
    `uci == best_uci` and never re-searches it), so this is faithfully reproduced,
    not introduced. The client short-circuits identically.
12. **Do not use the nmrugg loader's documented `#<wasm-url>` hash override.** With it
    the worker loads but never reports `uciok`, so boot hangs forever with no error.
    The default — same directory, `.js` → `.wasm` — is what the layout needs anyway.
13. **Every threaded Stockfish wasm build needs `SharedArrayBuffer`**, which needs
    COOP/COEP headers, which GitHub Pages cannot set. The full single-threaded build
    is 107.8 MB, past GitHub's 100 MiB per-file push limit (and Pages does not resolve
    Git LFS pointers). `stockfish-18-lite-single` (7.0 MB, NNUE embedded, no separate
    `.nnue`) is the only build that fits. `coi-serviceworker` would collide with our
    own service worker — only one can control a scope.
14. **Store the original `Response` in the wasm cache, headers intact.** Rebuilding it
    as a Blob drops `Content-Type: application/wasm`, which silently disables
    streaming compilation and V8's compiled-code cache — seconds per launch.
15. **`ThreadingHTTPServer`, not `TCPServer`, in `serve_dist.py`.** A single-threaded
    server deadlocks the instant the browser opens its second keep-alive connection.
16. On Windows the stdlib reads MIME types from the registry, which has no `.wasm`
    entry — `serve_dist.py` sets `application/wasm` explicitly.
17. **Chessground's stock coordinate CSS is misaligned outside lichess.**
    `chessground.base.css` hardcodes `coords.ranks { top: -20px }` and
    `coords.files { left: 24px }` — offsets that assume lichess's own board
    margins — plus a fixed `font-size: 9px` and a `translateY(39%)` nudge. Used
    as shipped, every label sits up and to the right of the square it names, at
    a size that ignores the board. `style.css` rebuilds them lichess-style:
    ranks in the top-right of the right-hand column, files in the bottom-left of
    the bottom row, sized from the square by `fitBoard()` via `--coord-size`.
    Three traps in that rebuild, all of which cost a round trip:
    - **`#board` *is* the `.cg-wrap` element.** Chessground reuses the mount node
      rather than nesting inside it, so `#board .cg-wrap …` matches nothing;
      write `#board coords …`.
    - **Pieces are `z-index: 2`, coords default to `auto`** — a piece on an edge
      square simply painted over its label. Coords are `z-index: 3` now.
    - **The brown theme colours coords itself**, keyed off each strip's sibling
      index (`.cg-wrap coords.files:nth-child(even) coord:nth-child(even)`),
      which outweighs a plain `.cg-wrap coords.files coord:…`. Hence the `#board`
      prefix. Contrast follows the square: the right column starts light at
      rank 1 while the bottom row starts dark at a1, so ranks and files run in
      opposite phase, and `.black` inverts both.

    Verified against the true square centres and colours in both orientations,
    and the occlusion fix was tested with a control (reverting the z-index puts
    `piece` back on top). Note both coords and pieces set `pointer-events: none`,
    so `elementFromPoint` returns `cg-board` for either and cannot tell them
    apart — force them hittable first or the test proves nothing.
    **Do not "fix" any of this by editing `web/vendor/chessground/` —
    re-vendoring would silently undo it.**

## Conventions

- Win%/judgment math mirrors Lichess: `Win% = 50 + 50*(2/(1+exp(-0.00368208*cp))-1)`,
  mate collapsed to ±10000 cp. This stays the *internal* classifier (it correctly
  ignores a 2-pawn swing in an already-won game); everything user-facing is in pawns.
- Evals are stored white-relative; convert to mover perspective via `judgments.win_pct`.
- Discarded puzzles keep their row (`discarded_at` set) so re-analysis never
  resurrects them; re-analysis deletes and rebuilds non-discarded mistakes + their
  attempts/scheduling.
- Anything that must survive a rebuild keys off `pid` (`"<game_id>:<ply>"`), never
  `mistakes.id`. `accept_sets` follows the same rule.
- The frontend is shared. A change to `web/` must work in **both** modes: check the
  dev server (`trainer`, 8123) and the built site (`trainer-static`, 8124, which
  mounts under `/chess-trainer/` so subpath bugs surface). Paths in HTML/JS are
  relative (`./css/...`), never absolute.
- `srs.js`, `judgments.js` and `rules.js` are line-for-line ports of `srs.py`,
  `judgments.py` and the python-chess helpers in `web.py`/`analysis.py`. Change them
  together, and re-run the equivalence check over all puzzle FENs when touching
  `rules.js` (dests, endgame type, move parsing and SAN were verified 1060/1060).
