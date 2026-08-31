import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

/**
 * `public/sw.js` never passes through the bundler and never runs in jsdom's
 * window realm, so nothing else in this suite would ever execute it. It is also
 * the only file in the PWA change with real runtime risk: a worker that throws
 * or that resolves `respondWith` with nothing serves a white page for a bundle
 * the browser could have fetched itself.
 *
 * The harness therefore evaluates the real source against hand-built worker
 * globals. `new Function` is what makes the re-evaluation per test possible --
 * `ASSETS_PREFIX` is read from `registration.scope` at evaluation time, so a
 * module-level import could only ever observe one scope.
 *
 * The path is assembled in two steps on purpose: vite rewrites the literal
 * `new URL('...', import.meta.url)` form into an asset URL, so that spelling
 * would read `/sw.js` off the filesystem root instead of the file next door.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const RAW = readFileSync(resolve(HERE, '../public/sw.js'), 'utf8');

/**
 * The server's `/sw.js` route substitutes the running chainlit version for this
 * placeholder before the bytes reach a browser. Doing the same here keeps the
 * tests honest about what actually runs, and keeps them from asserting on a
 * literal the release pipeline replaces.
 */
const BUILD_PLACEHOLDER = '__CHAINLIT_BUILD__';
const BUILD = '3.0.0-test';
const CACHE_NAME = `chainlit-assets-${BUILD}`;
const SOURCE = RAW.split(BUILD_PLACEHOLDER).join(BUILD);

const ORIGIN = 'https://app.example';

type Boot = {
  scope?: string;
  origin?: string;
  cacheNames?: string[];
  match?: any;
  put?: any;
  network?: any;
};

/**
 * A response the worker will agree to cache. Deliberately a plain object rather
 * than a real `Response`: undici's has `type: 'default'`, so sw.js's
 * `response.type === 'basic'` guard would skip the `cache.put` branch entirely
 * and the caching tests below would pass without ever caching anything.
 */
const basicResponse = () => ({
  ok: true,
  type: 'basic',
  clone: () => ({ cloned: true })
});

const boot = ({
  scope = `${ORIGIN}/`,
  origin = ORIGIN,
  cacheNames = [],
  match = undefined,
  put = vi.fn().mockResolvedValue(undefined),
  network = basicResponse()
}: Boot = {}) => {
  const handlers: Record<string, (event: any) => void> = {};

  const cache = {
    match: vi.fn().mockResolvedValue(match),
    put
  };
  const caches = {
    open: vi.fn().mockResolvedValue(cache),
    keys: vi.fn().mockResolvedValue(cacheNames),
    delete: vi.fn().mockResolvedValue(true)
  };
  const fetch = vi.fn().mockResolvedValue(network);

  const self = {
    addEventListener: (type: string, handler: (event: any) => void) => {
      handlers[type] = handler;
    },
    skipWaiting: vi.fn(),
    location: { origin },
    registration: { scope },
    clients: { claim: vi.fn().mockResolvedValue(undefined) }
  };

  // `self`, `caches` and `fetch` are free identifiers in the worker source, so
  // making them parameters is what binds them to the stubs above -- no global
  // is patched and no test can leak one into the next.
  new Function('self', 'caches', 'fetch', SOURCE)(self, caches, fetch);

  return { handlers, self, caches, cache, fetch, network };
};

const request = (url: string, method = 'GET') => ({ method, url });

const dispatchFetch = (handlers: Record<string, any>, req: any) => {
  const event = { request: req, respondWith: vi.fn() };
  handlers.fetch(event);
  return event;
};

const dispatchActivate = async (handlers: Record<string, any>) => {
  let settled: Promise<unknown> = Promise.resolve();
  handlers.activate({
    waitUntil: (promise: Promise<unknown>) => {
      settled = promise;
    }
  });
  await settled;
};

describe('sw.js — cache name', () => {
  it('carries the build placeholder the server stamps per release', () => {
    // The backend's `/sw.js` route rewrites this exact token. If it stops
    // matching, every release ships byte-identical worker source and the
    // browser never installs a new one.
    expect(RAW).toContain(`'chainlit-assets-${BUILD_PLACEHOLDER}'`);
  });
});

describe('sw.js — requests it refuses to answer', () => {
  it('ignores a non-GET request to /assets/', () => {
    const { handlers, fetch } = boot();

    const event = dispatchFetch(
      handlers,
      request(`${ORIGIN}/assets/index-abc.js`, 'POST')
    );

    expect(event.respondWith).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('ignores a cross-origin /assets/ request', () => {
    const { handlers } = boot();

    const event = dispatchFetch(
      handlers,
      request('https://cdn.example/assets/index-abc.js')
    );

    expect(event.respondWith).not.toHaveBeenCalled();
  });

  it('ignores a same-origin request outside /assets/', () => {
    const { handlers } = boot();

    const event = dispatchFetch(handlers, request(`${ORIGIN}/project/config`));

    expect(event.respondWith).not.toHaveBeenCalled();
  });
});

describe('sw.js — serving /assets/', () => {
  it('serves a cache hit without touching the network', async () => {
    const cached = { cached: true };
    const { handlers, caches, fetch } = boot({ match: cached });

    const event = dispatchFetch(
      handlers,
      request(`${ORIGIN}/assets/index-abc.js`)
    );

    await expect(event.respondWith.mock.calls[0][0]).resolves.toBe(cached);
    expect(caches.open).toHaveBeenCalledWith(CACHE_NAME);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('falls through to the network on a miss and caches the result', async () => {
    const { handlers, cache, fetch, network } = boot();
    const req = request(`${ORIGIN}/assets/index-abc.js`);

    const event = dispatchFetch(handlers, req);

    await expect(event.respondWith.mock.calls[0][0]).resolves.toBe(network);
    expect(fetch).toHaveBeenCalledWith(req);
    expect(cache.put).toHaveBeenCalledWith(req, { cloned: true });
  });

  it('still serves the network response when the cache write is rejected', async () => {
    // The quota case. Awaiting the put would reject `respondWith` and the page
    // would see a network error for a script it needs -- a white page instead
    // of a slow one.
    const put = vi.fn().mockRejectedValue(new Error('QuotaExceededError'));
    const { handlers, network } = boot({ put });

    const event = dispatchFetch(
      handlers,
      request(`${ORIGIN}/assets/index-abc.js`)
    );

    await expect(event.respondWith.mock.calls[0][0]).resolves.toBe(network);
    expect(put).toHaveBeenCalled();
  });
});

describe('sw.js — activate sweep', () => {
  it('retires our older caches and leaves everyone else alone', async () => {
    const { handlers, caches, self } = boot({
      cacheNames: [CACHE_NAME, 'chainlit-assets-old', 'somebody-elses-cache']
    });

    await dispatchActivate(handlers);

    expect(caches.delete).toHaveBeenCalledWith('chainlit-assets-old');
    expect(caches.delete).not.toHaveBeenCalledWith('somebody-elses-cache');
    expect(caches.delete).not.toHaveBeenCalledWith(CACHE_NAME);
    expect(caches.delete).toHaveBeenCalledTimes(1);
    expect(self.clients.claim).toHaveBeenCalled();
  });
});

describe('sw.js — scope-relative assets prefix', () => {
  it('matches /app/assets/ and not /assets/ when the scope is /app/', () => {
    const { handlers } = boot({ scope: `${ORIGIN}/app/` });

    const scoped = dispatchFetch(
      handlers,
      request(`${ORIGIN}/app/assets/index-abc.js`)
    );
    const rootLevel = dispatchFetch(
      handlers,
      request(`${ORIGIN}/assets/index-abc.js`)
    );

    expect(scoped.respondWith).toHaveBeenCalled();
    expect(rootLevel.respondWith).not.toHaveBeenCalled();
  });
});
