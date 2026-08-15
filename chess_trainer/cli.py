"""Command-line entry point: python -m chess_trainer <command>."""

import argparse
import os

from . import analysis, db, lichess, load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="chess_trainer", description="Personal chess trainer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Download new games from Lichess")
    p_sync.add_argument("--file", help="Import a manually downloaded NDJSON export instead of calling the API")

    p_analyze = sub.add_parser("analyze", help="Analyze pending games with Stockfish")
    p_analyze.add_argument("--limit", type=int, help="Max games to analyze this run")
    p_analyze.add_argument("--nodes", type=int, help="Engine nodes per position")
    p_analyze.add_argument("--order", choices=["newest", "oldest"], help="Queue order")

    p_serve = sub.add_parser("serve", help="Start the trainer web app")
    p_serve.add_argument("--port", type=int, help="Port (default from config.toml)")

    args = parser.parse_args()
    config = load_config()
    if args.command == "sync":
        conn = db.connect()
        if args.file:
            new, seen = lichess.import_file(conn, config["lichess"]["username"], args.file)
        else:
            new, seen = lichess.sync(conn, config["lichess"]["username"])
        total = conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]
        print(f"Fetched {seen} game(s), {new} new. Total in database: {total}.")
    elif args.command == "analyze":
        conn = db.connect()
        analysis.run(conn, config, limit=args.limit, nodes=args.nodes, order=args.order)
    elif args.command == "serve":
        import uvicorn
        # PORT env var lets launchers (e.g. the preview) assign a free port.
        port = args.port or int(os.environ.get("PORT") or 0) or config["trainer"].get("port", 8123)
        uvicorn.run("chess_trainer.web:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
