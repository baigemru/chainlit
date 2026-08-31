import getRouterBasename from '@/lib/router';

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

  // Both the script URL and the scope are anchored to `root_path`, never to the
  // origin root. Registering a literal `/sw.js` under a `root_path` deployment
  // asks the origin root for a worker this app does not serve: on a shared
  // origin that request is answered by whatever *other* app lives there, and
  // the browser would then install a foreign worker with scope `/` -- which
  // controls this app too. The explicit scope is the same directory the script
  // is served from, so it is what the browser would default to; stating it
  // keeps the guarantee visible.
  const basename = getRouterBasename();

  navigator.serviceWorker
    .register(`${basename}/sw.js`, { scope: `${basename}/` })
    .catch((error) => {
      console.warn('Service worker registration failed', error);
    });
}
