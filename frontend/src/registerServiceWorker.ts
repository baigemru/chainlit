/**
 * Registration of the asset cache worker, kept out of `main.tsx` so it can be
 * exercised without booting the whole app.
 *
 * It refuses to register in dev: Vite serves modules unbundled from paths that
 * have no content hash, and a worker caching those would serve yesterday's
 * source after an edit. It also refuses to assume `navigator.serviceWorker`
 * exists -- it is absent in a non-secure context and in some in-app browsers,
 * and a bootstrap that throws there takes the whole chat down for a caching
 * nicety. A failed registration is a warning, never an error the app sees.
 */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD) return;
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return;
  }

  navigator.serviceWorker.register('/sw.js').catch((error) => {
    console.warn('Service worker registration failed', error);
  });
}
