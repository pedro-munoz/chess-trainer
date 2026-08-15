"""Import Claude-written explanations from data/explain_work/out/*.json into
the mistakes table (explanation_source='claude').

Each file is a JSON array of {"id": int, "explanation": str,
"explanation_played": str}. Idempotent: re-importing overwrites the same rows.
"""

import json
from pathlib import Path

from chess_trainer import DATA_DIR, db

OUT_DIR = DATA_DIR / "explain_work" / "out"
MAX_LEN = 900


def main() -> None:
    conn = db.connect()
    known = {r["id"] for r in conn.execute("SELECT id FROM mistakes")}
    updated, skipped = 0, []
    for path in sorted(OUT_DIR.glob("*.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            skipped.append(f"{path.name}: bad JSON ({e})")
            continue
        for e in entries:
            mid = e.get("id")
            idea = (e.get("explanation") or "").strip()
            played = (e.get("explanation_played") or "").strip()
            if mid not in known:
                skipped.append(f"{path.name}: unknown id {mid}")
                continue
            if not idea or not played:
                skipped.append(f"{path.name}: id {mid} empty field")
                continue
            if len(idea) > MAX_LEN or len(played) > MAX_LEN:
                skipped.append(f"{path.name}: id {mid} too long "
                               f"({len(idea)}/{len(played)} chars)")
                continue
            conn.execute(
                "UPDATE mistakes SET explanation = ?, explanation_played = ?, "
                "explanation_source = 'claude' WHERE id = ?",
                (idea, played, mid))
            updated += 1
    conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM mistakes WHERE explanation_source = 'claude'"
    ).fetchone()["n"]
    print(f"Imported {updated} explanations; {total} puzzles now have Claude explanations.")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for s in skipped[:20]:
            print("  -", s)
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")


if __name__ == "__main__":
    main()
