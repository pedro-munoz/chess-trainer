# Stockfish (WebAssembly)

`stockfish-18-lite-single.js` and `stockfish-18-lite-single.wasm` are vendored
**byte-for-byte unmodified** from the upstream release:

- Release: <https://github.com/nmrugg/stockfish.js/releases/tag/v18.0.0>
- Upstream project: <https://github.com/nmrugg/stockfish.js>
- Stockfish itself: <https://github.com/official-stockfish/Stockfish>

Stockfish is free software under the **GNU General Public License v3**; the full
text is in `Copying.txt` and the contributor list in `AUTHORS`. Because this site
conveys the compiled engine, the corresponding source is the upstream release
linked above, from which these files came without modification.

## Why this build

The `lite-single` flavour is the only practical one here:

| build | size | needs SharedArrayBuffer |
|---|---|---|
| `stockfish-18.wasm` (full, threaded) | 107.8 MB | yes |
| `stockfish-18-single.wasm` (full) | 107.8 MB | no |
| `stockfish-18-lite.wasm` (threaded) | 6.8 MB | yes |
| **`stockfish-18-lite-single.wasm`** | **7.0 MB** | **no** |

GitHub Pages cannot set the COOP/COEP headers `SharedArrayBuffer` requires, which
rules out every threaded build. The full single-threaded build is over GitHub's
100 MiB per-file push limit (and Git LFS objects are not resolved by Pages), so
it cannot be committed at all.

The NNUE network is embedded in the `.wasm` — there is no separate `.nnue` to
fetch.
