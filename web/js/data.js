/* The exported dataset (static build only): puzzles, games, insights.

   Loaded once and memoized. Puzzle records use short keys to keep the payload
   small — see scripts/export_static.py for the field map. */

let cache = null;

async function fetchJson(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
  return resp.json();
}

export async function load() {
  if (cache) return cache;
  const [manifest, puzzles, games, insights] = await Promise.all([
    fetchJson('./data/manifest.json'),
    fetchJson('./data/puzzles.json'),
    fetchJson('./data/games.json'),
    fetchJson('./data/insights.json'),
  ]);
  cache = {
    manifest,
    puzzles,
    games,
    insights,
    byPid: new Map(puzzles.map((p) => [p.p, p])),
  };
  return cache;
}

/** Reshape a stored puzzle into the /api/next `puzzle` payload. */
export function toPuzzlePayload(p, games, dests) {
  const gameId = p.p.slice(0, p.p.lastIndexOf(':'));
  const g = games[gameId] || {};
  return {
    id: p.p,
    pid: p.p,
    fen: p.f,
    color: p.c,
    played_san: p.ps,
    played_uci: p.pu,
    judgment: p.j,
    phase: p.ph,
    motif: p.mo ?? null,
    opening: g.op ?? null,
    move_number: p.n,
    dests,
    game: {
      id: gameId,
      url: `https://lichess.org/${gameId}`,
      opponent: g.o ?? null,
      speed: g.s ?? null,
      played_at: g.t ?? null,
      user_rating: g.ur ?? null,
      opponent_rating: g.or ?? null,
    },
  };
}
