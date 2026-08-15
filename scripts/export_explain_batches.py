"""Export puzzle context as JSON batches for Claude Code to write detailed
explanations (see CLAUDE.md, "Detailed explanations").

Each batch file carries everything needed to explain a puzzle without DB
access: position, best line, refutation line, what actually happened in the
game, and the eval swing. Claude's output is written back with
scripts/import_claude_explanations.py.
"""

import argparse
import json
from pathlib import Path

import chess

from chess_trainer import DATA_DIR, db, judgments
from chess_trainer.analysis import _san_line

OUT_DIR = DATA_DIR / "explain_work"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=40)
    ap.add_argument("--all", action="store_true",
                    help="Include puzzles that already have Claude explanations")
    args = ap.parse_args()

    conn = db.connect()
    where = "m.discarded_at IS NULL"
    if not args.all:
        where += " AND m.explanation_source != 'claude'"
    rows = conn.execute(
        f"""SELECT m.*, g.opening, g.result, g.moves, g.opponent, g.opponent_rating
            FROM mistakes m JOIN games g ON g.id = m.game_id
            WHERE {where} ORDER BY m.id"""
    ).fetchall()

    items = []
    for r in rows:
        board = chess.Board(r["fen"])
        pov = board.turn
        ev_before = conn.execute(
            "SELECT pv, score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
            (r["game_id"], r["ply"])).fetchone()
        ev_after = conn.execute(
            "SELECT pv, score_cp, score_mate FROM evals WHERE game_id = ? AND ply = ?",
            (r["game_id"], r["ply"] + 1)).fetchone()
        board_after = board.copy()
        board_after.push(chess.Move.from_uci(r["played_uci"]))
        punish_san = (_san_line(board_after, (ev_after["pv"] or "").split(), 10)
                      if ev_after else "")
        game_san = r["moves"].split()
        items.append({
            "id": r["id"],
            "fen": r["fen"],
            "color": r["color"],
            "move_number": r["ply"] // 2 + 1,
            "played_san": r["played_san"],
            "best_san": r["best_san"],
            "best_line_san": r["pv_san"] or "",
            "punish_line_san": punish_san,
            "eval_best": judgments.fmt_eval(ev_before["score_cp"], ev_before["score_mate"],
                                            pov) if ev_before else None,
            "eval_after_played": judgments.fmt_eval(ev_after["score_cp"], ev_after["score_mate"],
                                                    pov) if ev_after else None,
            "win_loss": r["win_loss"],
            "judgment": r["judgment"],
            "motif": r["motif"] or "positional",
            "phase": r["phase"],
            "opening": r["opening"],
            "game_continuation_san": " ".join(game_san[r["ply"]:r["ply"] + 8]),
            "game_result": r["result"],
        })

    batches_dir = OUT_DIR / "batches"
    out_dir = OUT_DIR / "out"
    batches_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    # A fresh export starts a fresh work cycle: clear both sides, so a later
    # import can't silently re-apply last cycle's outputs.
    for old in batches_dir.glob("batch_*.json"):
        old.unlink()
    for old in out_dir.glob("batch_*.json"):
        old.unlink()

    for i in range(0, len(items), args.batch_size):
        n = i // args.batch_size + 1
        path = batches_dir / f"batch_{n:03d}.json"
        path.write_text(json.dumps(items[i:i + args.batch_size], indent=1),
                        encoding="utf-8")
    n_batches = (len(items) + args.batch_size - 1) // args.batch_size
    print(f"Exported {len(items)} puzzles into {n_batches} batch files in {batches_dir}")
    print(f"Write explanation files to {out_dir}")


if __name__ == "__main__":
    main()
