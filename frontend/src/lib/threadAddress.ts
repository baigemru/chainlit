/**
 * The address bar, read and written as the one place a thread is requested.
 *
 * Pure on purpose. `threadIdFromLocation` runs before `RecoilRoot` mounts --
 * before the router exists at all -- to seed the session descriptor, and
 * `threadAddressFor` runs inside a socket callback. Neither may reach for a
 * hook, and both have to agree with react-router about what the path means:
 * if the seeded thread and `useParams().id` ever disagree on the first
 * commit, `AutoResumeThread` reads that as "this tab asked for a different
 * thread" and clears the session the server had just kept.
 */

const THREAD_PREFIX = '/thread/';

/**
 * react-router's own basename strip, mirrored.
 *
 * `@remix-run/router`'s `stripBasename` (dist/router.cjs.js:1164): a
 * case-insensitive prefix, a tolerated trailing slash on the basename, and
 * the character after it must be a boundary -- so `/pandax/...` is not
 * inside `/panda`. Returns null when the path is outside the basename,
 * exactly as the router does before refusing to render anything.
 */
const stripBasename = (pathname: string, basename: string): string | null => {
  if (!basename || basename === '/') return pathname;
  if (!pathname.toLowerCase().startsWith(basename.toLowerCase())) return null;
  const start = basename.endsWith('/') ? basename.length - 1 : basename.length;
  const next = pathname.charAt(start);
  if (next && next !== '/') return null;
  return pathname.slice(start) || '/';
};

/** Trailing slash removed, `/` kept as `/`. */
const trimTrailingSlash = (pathname: string): string =>
  pathname.length > 1 && pathname.endsWith('/')
    ? pathname.slice(0, -1)
    : pathname;

/**
 * The thread `/thread/:id` asks for, or undefined for every other route.
 *
 * `basename` is what `getRouterBasename()` returns, so pass a raw
 * `window.location.pathname`; inside the router the pathname is already
 * stripped and the basename is `''`.
 */
export const threadIdFromLocation = (
  pathname: string,
  basename: string
): string | undefined => {
  const routed = stripBasename(pathname || '/', basename);
  if (routed === null) return undefined;
  const path = trimTrailingSlash(routed);
  if (!path.startsWith(THREAD_PREFIX)) return undefined;
  const raw = path.slice(THREAD_PREFIX.length);
  // One segment only: `/thread/t1/anything` is not the thread route, and
  // the router would not match it as one either.
  if (!raw || raw.includes('/')) return undefined;
  try {
    return decodeURIComponent(raw);
  } catch (_error) {
    // A malformed escape is not a reason to fail the page load; the id is
    // taken as typed and the server will answer with whatever it gives us.
    return raw;
  }
};

/**
 * Where the address has to move now that the server has named the thread,
 * or null when it must not move at all.
 *
 * The return value is a react-router path with no basename: `navigate`
 * re-joins it (`react-router` dist/umd/react-router.development.js:261), so
 * including it here would double the prefix.
 *
 * Only the chat routes move. `/share/:id` and `/element/:id` are views of
 * something other than this session, and yanking them to the live
 * conversation because a socket connected would lose the page the user
 * opened.
 */
export const threadAddressFor = (
  pathname: string,
  basename: string,
  threadId: string
): string | null => {
  const routed = stripBasename(pathname || '/', basename);
  if (routed === null) return null;
  const path = trimTrailingSlash(routed);
  const isChatRoute =
    path === '/' || path === '/thread' || path.startsWith(THREAD_PREFIX);
  if (!isChatRoute) return null;
  // Already there: a navigate per reconnect would fill the history stack
  // with the same entry and leave the Back button pointing at itself.
  if (threadIdFromLocation(path, '') === threadId) return null;
  // A nested path under /thread/ is not the thread route and is not ours.
  if (
    path.startsWith(THREAD_PREFIX) &&
    threadIdFromLocation(path, '') === undefined
  ) {
    return null;
  }
  return `${THREAD_PREFIX}${encodeURIComponent(threadId)}`;
};
