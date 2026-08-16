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

    p_state = sub.add_parser(
        "sync-state", help="Exchange training state with your other devices")
    p_state.add_argument("--init", action="store_true",
                         help="Write a blank data/gist_auth.json and exit")
    p_state.add_argument("--pull-only", action="store_true",
                         help="Take the other devices' state without publishing ours")
    p_state.add_argument("--push-only", action="store_true",
                         help="Publish ours without taking theirs")
    p_state.add_argument("--dry-run", action="store_true",
                         help="Report what would change, write nothing")

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
    elif args.command == "sync-state":
        from . import gist, sync_state
        if args.init:
            print(sync_state.write_auth_template())
            return
        conn = db.connect()
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            counts = sync_state.run(conn, pull=not args.push_only,
                                    push=not args.pull_only, dry_run=args.dry_run)
        except gist.GistError as exc:
            raise SystemExit(f"sync failed: {exc}") from exc
        print(sync_state.report(counts))
        if args.dry_run:
            # Plain ASCII: the Windows console codepage mangles an em dash here.
            print("\n(dry run - nothing written)")


if __name__ == "__main__":
    main()
