"""Serve dist/ the way GitHub Pages will, for local verification.

    python -m scripts.serve_dist [--port 8124] [--base /chess-trainer/]

Two things this gets right that a bare http.server does not:

- .wasm is served as application/wasm. On Windows the stdlib reads MIME types
  from the registry, which does not know about wasm, and the wrong type quietly
  disables WebAssembly streaming compilation.
- --base mounts the site under a subpath, so the relative-path layout is
  exercised the same way a GitHub Pages project site exercises it.
"""

import argparse
import functools
import http.server
import os

from chess_trainer import PROJECT_ROOT

DIST = PROJECT_ROOT / "dist"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".webmanifest": "application/manifest+json",
        ".woff2": "font/woff2",
    }
    base = "/"

    def translate_path(self, path):
        if self.base != "/":
            if path == self.base.rstrip("/"):
                path = self.base
            if path.startswith(self.base):
                path = "/" + path[len(self.base):]
        return super().translate_path(path)

    def send_response(self, code, *args):
        super().send_response(code, *args)
        # Pages serves everything with a short max-age; the service worker is
        # what actually caches. Mirror that rather than letting the browser
        # hold a stale build between test runs.
        self.send_header("Cache-Control", "no-cache")

    def log_message(self, fmt, *args):
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            super().log_message(fmt, *args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8124)))
    ap.add_argument("--base", default="/", help="subpath to mount under, e.g. /chess-trainer/")
    args = ap.parse_args()

    if not DIST.exists():
        raise SystemExit("dist/ not found — run: python -m scripts.export_static")

    base = args.base if args.base.endswith("/") else args.base + "/"
    handler = functools.partial(Handler, directory=str(DIST))
    Handler.base = base

    # Threaded: browsers hold several keep-alive connections open at once, and a
    # single-threaded server deadlocks the moment the page loads more than one
    # asset in parallel.
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Serving {DIST} at http://localhost:{args.port}{base}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
