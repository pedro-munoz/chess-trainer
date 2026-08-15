from chess_trainer import db

conn = db.connect()
g = conn.execute("SELECT COUNT(*) AS t, COUNT(analyzed_at) AS a FROM games").fetchone()
print(f"games analyzed: {g['a']}/{g['t']}")
for r in conn.execute(
    "SELECT judgment, COUNT(*) AS n FROM mistakes WHERE discarded_at IS NULL "
    "GROUP BY judgment ORDER BY n DESC"
):
    print(f"  {r['judgment']}: {r['n']}")
skipped = conn.execute(
    "SELECT COUNT(*) AS n FROM games WHERE analyzed_at IS NULL"
).fetchone()["n"]
if skipped:
    for r in conn.execute(
        "SELECT variant, COUNT(*) AS n FROM games WHERE analyzed_at IS NULL GROUP BY variant"
    ):
        print(f"  not analyzed ({r['variant']}): {r['n']}")
