// The service worker exists for one reason: to make an installed app open
// without waiting on the network for its own bundle. It therefore caches
// exactly one thing -- `/assets/`, which Vite writes with a content hash in
// every filename, so a cached entry can never be a stale version of anything.
//
// Everything else is refused on purpose. `index.html` is assembled per request
// from live config (the injection placeholders), so a cached copy would pin the
// browser to the settings of whenever it was first installed. `/project/*` is
// the API, `/public/*` is host-supplied and editable, and the websocket upgrade
// is not a request a cache has any business seeing. For all of them the handler
// returns without calling `respondWith`, which hands the request back to the
// browser untouched -- not an empty response, not a pass-through fetch that
// would quietly drop credentials and upgrade headers.

const CACHE_NAME = 'chainlit-assets-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.map((name) => (name === CACHE_NAME ? null : caches.delete(name)))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith('/assets/')) return;

  event.respondWith(
    (async () => {
      // Scoped to our own cache rather than `caches.match`, which searches
      // every cache on the origin -- a hit from someone else's is not ours to
      // serve.
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(request);
      if (cached) return cached;

      const response = await fetch(request);
      // Only a plain 200 is worth keeping: an opaque or error response stored
      // here would be replayed on every later load with no way to recover.
      if (response.ok && response.type === 'basic') {
        await cache.put(request, response.clone());
      }
      return response;
    })()
  );
});
