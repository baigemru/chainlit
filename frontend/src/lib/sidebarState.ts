/**
 * The desktop sidebar's last state, as the vendored shadcn provider already
 * records it: `setOpen` writes `sidebar:state` on every toggle
 * (`components/ui/sidebar.tsx`) and nothing ever read it back. Reading it is
 * the missing half, not a second store — a localStorage key beside it would
 * leave the cookie being written and ignored.
 */

const SIDEBAR_COOKIE_NAME = 'sidebar:state';

/**
 * The remembered state, or undefined for a browser that never toggled.
 * Anything that is not exactly `true` or `false` counts as absent: the jar is
 * shared with the rest of the deployment, and a value we did not write is not
 * a state we can honour.
 */
export function readSidebarCookie(cookie: string): boolean | undefined {
  for (const part of cookie.split(';')) {
    const separator = part.indexOf('=');
    if (separator < 0) continue;
    if (part.slice(0, separator).trim() !== SIDEBAR_COOKIE_NAME) continue;
    const value = part.slice(separator + 1).trim();
    if (value === 'true') return true;
    if (value === 'false') return false;
    return undefined;
  }
  return undefined;
}

/**
 * What `SidebarProvider` opens with. The config is the default for a browser
 * that has never expressed a preference; the cookie is the preference. Because
 * `defaultOpen` is a `useState` initialiser the provider re-reads this on every
 * remount — which is what carries the state across `/` → `/thread/<id>`, where
 * the route swaps the whole page element under it.
 */
export function resolveSidebarDefault(
  cookie: string,
  defaultSidebarState?: 'open' | 'closed' | 'hidden'
): boolean {
  return readSidebarCookie(cookie) ?? defaultSidebarState !== 'closed';
}
