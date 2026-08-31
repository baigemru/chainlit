import { afterEach, describe, expect, it, vi } from 'vitest';

import { registerServiceWorker } from '../src/registerServiceWorker';

/**
 * `vi.stubEnv` writes through `process.env`, which stringifies -- stubbing
 * `PROD` to the boolean `false` would leave the code reading the string
 * `'false'`, which is truthy, and the dev case below would then pass against a
 * helper that ignores the flag entirely. The empty string is the only falsy
 * value that survives the round trip.
 */
const PROD = 'true';
const DEV = '';

/**
 * jsdom's navigator has no `serviceWorker`, so the unsupported-browser case is
 * the bare environment; installing the stub is what makes a browser that has
 * one.
 */
const installServiceWorker = (
  register = vi.fn().mockResolvedValue(undefined)
) => {
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { register },
    configurable: true
  });
  return register;
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  delete (navigator as any).serviceWorker;
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('registerServiceWorker', () => {
  it('registers /sw.js in a production build on a browser that supports it', () => {
    vi.stubEnv('PROD', PROD);
    const register = installServiceWorker();

    registerServiceWorker();

    expect(register).toHaveBeenCalledTimes(1);
    expect(register).toHaveBeenCalledWith('/sw.js');
  });

  it('does not touch navigator when serviceWorker is absent', () => {
    vi.stubEnv('PROD', PROD);
    expect('serviceWorker' in navigator).toBe(false);

    // The whole app bootstraps through this call; a throw here would take the
    // chat down on every browser without a worker, cache or no cache.
    expect(() => registerServiceWorker()).not.toThrow();
  });

  it('does not register in dev, where assets have no content hash', () => {
    vi.stubEnv('PROD', DEV);
    const register = installServiceWorker();

    registerServiceWorker();

    expect(register).not.toHaveBeenCalled();
  });

  it('swallows a rejected registration as a warning', async () => {
    vi.stubEnv('PROD', PROD);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    installServiceWorker(vi.fn().mockRejectedValue(new Error('insecure')));

    registerServiceWorker();
    await flush();

    expect(warn).toHaveBeenCalled();
  });
});
