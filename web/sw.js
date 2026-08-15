/* Service worker for the static build.

   Three caches, on purpose:

   - shell-<build>  the app and its data, precached on install so the very
                    first offline session works. Replaced wholesale each build.
   - engine-v1      the wasm engine, ~7 MB. Deliberately NOT precached (that
                    would stall install behind a 7 MB download) and deliberately
                    NOT swept when the shell rotates, so a redeploy never
                    re-downloads it over mobile data.
   - the manifest   network-first, so a new build is noticed promptly.

   scripts/export_static.py fills in __BUILD_ID__ and '__PRECACHE__'. */

const BUILD_ID = '__BUILD_ID__';
const SHELL_CACHE = `shell-${BUILD_ID}`;
const ENGINE_CACHE = 'engine-v1';
const MANIFEST = './data/manifest.json';
const PRECACHE = '__PRECACHE__';

const isEngine = (url) => url.pathname.includes('/vendor/stockfish/');
const isManifest = (url) => url.pathname.endsWith('/data/manifest.json');

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // Individually, so one bad entry cannot fail the whole install.
    await Promise.all(PRECACHE.map((path) =>
      cache.add(new Request(path, { cache: 'reload' })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter((k) => k !== SHELL_CACHE && k !== ENGINE_CACHE)
      .map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

/* The engine is immutable and versioned by filename: cache-first forever.

   The original Response is stored and returned untouched. Rebuilding it as a
   Blob would drop `Content-Type: application/wasm`, which costs both streaming
   compilation and V8's compiled-code cache — a multi-second penalty on every
   launch. */
async function engineFirst(request) {
  const cache = await caches.open(ENGINE_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const resp = await fetch(request);
  if (resp.ok) {
    try {
      await cache.put(request, resp.clone());
    } catch {
      // Out of quota: drop older engine builds and try once more.
      for (const key of await cache.keys()) await cache.delete(key);
      await cache.put(request, resp.clone()).catch(() => {});
    }
  }
  return resp;
}

async function shellFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(request, { ignoreSearch: true });
  if (hit) return hit;
  try {
    const resp = await fetch(request);
    if (resp.ok && request.method === 'GET') cache.put(request, resp.clone());
    return resp;
  } catch (err) {
    // Offline and not precached: navigations still get the dashboard.
    if (request.mode === 'navigate') {
      const fallback = await cache.match('./index.html');
      if (fallback) return fallback;
    }
    throw err;
  }
}

async function manifestFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const resp = await fetch(request);
    if (resp.ok) cache.put(request, resp.clone());
    return resp;
  } catch {
    return (await cache.match(request, { ignoreSearch: true }))
        || Response.error();
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isEngine(url)) event.respondWith(engineFirst(request));
  else if (isManifest(url)) event.respondWith(manifestFirst(request));
  else event.respondWith(shellFirst(request));
});

self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});
