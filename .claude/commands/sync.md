---
description: Full daily pipeline — pull new games, analyze, write Claude explanations, deploy to the phone
argument-hint: "[--file <export.pgn>] [--limit N] [--nodes N]"
---

Run the complete "new games" pipeline from CLAUDE.md, end to end. Extra
arguments, if any: `$ARGUMENTS` (e.g. `--file C:/path/export.pgn` to import a
manual Lichess export instead of hitting the API; `--limit`/`--nodes` to cap the
analysis pass).

**Run everything from the primary checkout**, `C:\Users\pedro\Documents\Projects\chess`
— not a worktree. `data/trainer.db`, `.venv/` and `tools/stockfish/` are
gitignored and exist only there. Use `./.venv/Scripts/python.exe`.

This is one pipeline and **every step is mandatory**. Do not stop after
`analyze`, and do not offer the explanation or deploy steps as optional
follow-ups — a puzzle that reaches the phone with rule-generated text, or a
puzzle that never reaches the phone at all, means the job was left half done.
Analysis is slow (~15–25 s/game); let it run rather than trimming it.

## 1. Take the phone's state first

```
./.venv/Scripts/python.exe -m chess_trainer sync-state
```

Cheap, and it must come before analysis: a puzzle Pedro discarded on the phone
needs its `discarded_at` set before re-analysis or the next export can ship it
back to him. If it fails on credentials (`data/gist_auth.json`), say so and
carry on — the rest of the pipeline does not depend on it.

## 2. Download new games

```
./.venv/Scripts/python.exe -m chess_trainer sync
```

Incremental — only games newer than `meta.last_sync_created_at`. If Lichess
rate-limits or returns "Please only run 1 request(s) at a time", **do not
retry in a loop**; that extends the penalty. Stop and tell Pedro to download
https://lichess.org/@/Katutx0/download in a logged-in browser, then rerun this
command with `--file`.

If no new games arrived, say so and stop here — there is nothing to analyze,
explain or deploy.

## 3. Analyze

```
./.venv/Scripts/python.exe -m chess_trainer analyze
```

Picks up `analyzed_at IS NULL` only, commits per game. **Never re-analyze games
that are already analyzed** — a different node count changes some `best_uci`,
which changes the puzzle hash, which resets those cards on the phone.

Report how many games were analyzed and how many new puzzles came out.

## 4. Claude explanations (the narrative)

```
./.venv/Scripts/python.exe -m scripts.export_explain_batches
```

Then fan out subagents, **one per batch file**, concurrency **≤ 4**. Each agent
reads `data/explain_work/batches/batch_NNN.json` and writes
`data/explain_work/out/batch_NNN.json`.

Give each agent this brief (substituting its batch number):

> You are writing coaching text for one batch of chess puzzles from Pedro's own
> games. Read `data/explain_work/batches/batch_NNN.json` — an array of puzzles,
> each with `fen`, `color` (Pedro's side), `move_number`, `played_san`,
> `best_san`, `best_line_san`, `punish_line_san`, `eval_best`,
> `eval_after_played`, `judgment`, `motif`, `phase`, `opening`,
> `game_continuation_san`, `game_result`.
>
> Write `data/explain_work/out/batch_NNN.json`: an array of
> `{"id": <the puzzle's id>, "explanation": "...", "explanation_played": "..."}`
> covering **every** puzzle in the batch.
>
> - `explanation` — why the best move is best. Open with the move or its idea,
>   then the concrete line from `best_line_san`, then the resulting eval in
>   pawns.
> - `explanation_played` — what is wrong with what Pedro actually played, using
>   `punish_line_san` for the refutation, and close with what really happened in
>   the game (`game_continuation_san`, `game_result`) when it adds something.
> - Voice: a coach talking to his pupil. Second person ("your f8-rook", "you
>   untangle"). Concrete squares and piece names, never generic advice.
> - Ground every claim in the lines you were given. If a line looks odd, hedge
>   rather than assert — several "corrupt FEN" reports in the past turned out to
>   be misreadings. Verify with python-chess only when genuinely unsure.
> - ≤ 700 characters per field, ASCII punctuation, no markdown.
>
> **Write the output file early and complete, then refine it.** Do not spend
> your budget verifying before anything is on disk — a previous run lost six
> batches that way. If you need a helper script, name it after your batch
> number; the scratchpad is shared.

Then import:

```
./.venv/Scripts/python.exe -m scripts.import_claude_explanations
```

Read its output: any skipped ids (unknown / empty / >900 chars) must be fixed
and re-imported, not shrugged off. Then check the gate — it must print 0:

```
./.venv/Scripts/python.exe -c "import sqlite3;print(sqlite3.connect('data/trainer.db').execute(\"SELECT COUNT(*) FROM mistakes WHERE discarded_at IS NULL AND explanation_source='rules'\").fetchone()[0])"
```

## 5. Deploy to the phone

```
./.venv/Scripts/python.exe -m scripts.deploy_pages
```

Builds `dist/` (running the accept-set precompute, ~1.2 s per new puzzle) and
force-pushes it to `gh-pages`, amending so the branch stays one commit. This is
the step that actually reaches Pedro — the merge into `main` is not the
delivery, this is.

## 6. Then report

- games downloaded, games analyzed, new puzzles created
- puzzles explained, and the `rules`-source count (0)
- what `deploy_pages` published, and the live URL

Finally, compare the current game/mistake counts against `based_on` in
`data/insights_report.json`. If games have grown by roughly 10% or more since
that report, tell Pedro the coach report is stale and offer to refresh it —
that is a separate job (see CLAUDE.md, "Coach report"), not part of this one.
