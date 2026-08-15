# Chess Trainer

Learn from your own mistakes. This downloads your Lichess games, analyzes them
locally with Stockfish, turns every blunder, mistake and inaccuracy into a
puzzle, and drills them back at you with Anki-style spaced repetition.

**Live:** <https://pedro-munoz.github.io/chess-trainer/> — installable, and works
fully offline once loaded, engine included.

No Lichess credentials are used anywhere: only the public API and manual exports.

## How it works

```
sync      fetch new games (incremental, resumable)
analyze   Stockfish at 1M nodes/position -> evals -> mistakes
          judged with Lichess's own win% model and thresholds
explain   rule-based coaching text, upgraded by Claude Code
deploy    build a static snapshot and publish it
```

The analysis half is a Python package that runs on a laptop. The half you
actually train with is a static site — the same frontend, with the server
replaced by precomputed JSON and a WebAssembly Stockfish.

Almost everything the trainer needs is already known before you open it: the
puzzles, the explanations, the insights, and even which alternative moves count
as "also fine" (precomputed with the desktop engine, so a phone-sized search
never has to second-guess a laptop-sized one). The bundled engine only runs when
you explore a position freely after solving.

## Running it yourself

Needs Python 3.14, a Stockfish binary in `tools/stockfish/`, and a `config.toml`
pointing at your Lichess username.

```bash
python -m chess_trainer sync
python -m chess_trainer analyze
python -m chess_trainer serve        # dev server on :8123
python -m scripts.deploy_pages       # build + publish the static site
```

See `CLAUDE.md` for the full layout, the daily routine, and the accumulated
findings about what does and does not work.

## Credits

Bundled Stockfish is `stockfish-18-lite-single` from
[nmrugg/stockfish.js](https://github.com/nmrugg/stockfish.js), vendored
unmodified under the **GPL-3.0** — see `web/vendor/stockfish/` for the license,
authors, and the exact upstream release. Board rendering by
[chessground](https://github.com/lichess-org/chessground), move generation by
[chess.js](https://github.com/jhlywa/chess.js). The win-percentage model and
judgment thresholds follow Lichess's.
