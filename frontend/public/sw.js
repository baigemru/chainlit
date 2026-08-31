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

// `__CHAINLIT_BUILD__` is a placeholder the server's `/sw.js` route rewrites to
// the running chainlit version. Two things depend on it: the worker's bytes
// change every release, which is what makes the browser install a new one at
// all, and the cache name changes with it, so the activate sweep below retires
// the previous release's entries. The literal placeholder is what ships in the
// source tree -- dev never registers a worker (the PROD-only guard in
// `registerServiceWorker.ts`), so it is never read unstamped.
const CACHE_NAME = 'chainlit-assets-__CHAINLIT_BUILD__';

// The prefix comes from the worker's own scope, not from a literal `/assets/`.
// Behind a `root_path` the app is served from `/app/`, and a worker registered
// at `/app/sw.js` gets scope `/app/` -- matching a literal `/assets/` there
// would cache nothing at all, while matching it on a shared origin would mean
// answering for another app's bundle.
const ASSETS_PREFIX = new URL('./assets/', self.registration.scope).pathname;

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      // Only our own caches. This plugin is meant to be embedded in a host
      // application on the same origin; the fetch handler already refuses to
      // read a foreign cache, and the sweep must equally refuse to delete one.
      const ours = names.filter(
        (name) => name.startsWith('chainlit-assets-') && name !== CACHE_NAME
      );
      await Promise.all(ours.map((name) => caches.delete(name)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith(ASSETS_PREFIX)) return;

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
        // Deliberately not awaited, and its rejection is swallowed: a
        // QuotaExceededError must degrade to "not cached", never to "not
        // loaded". Awaiting it would reject the `respondWith` promise and the
        // page would see a network error for a script it needs -- a white page
        // instead of a slow one.
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    })()
  );
});
