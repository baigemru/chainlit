import { describe, expect, it } from 'vitest';

// A relative path, not the `@/` alias: `tests/tsconfig.json` includes only
// `**/*.spec.tsx`, so `vite-tsconfig-paths` maps nothing for a `.spec.ts`.
import {
  readSidebarCookie,
  resolveSidebarDefault
} from '../src/lib/sidebarState';

/**
 * The provider has written `sidebar:state` on every toggle since it was
 * vendored in; these are the rules for reading it back. The jar belongs to the
 * whole deployment, so the parser has to be as sceptical about what it finds
 * there as about finding nothing at all.
 */
describe('readSidebarCookie', () => {
  it('answers for a browser that never toggled', () => {
    expect(readSidebarCookie('')).toBeUndefined();
    expect(readSidebarCookie('theme=dark; panda_session=abc')).toBeUndefined();
  });

  it('finds the state among the deployment’s other cookies', () => {
    expect(readSidebarCookie('theme=dark; sidebar:state=true; a=1')).toBe(true);
    expect(readSidebarCookie('sidebar:state=false; theme=dark')).toBe(false);
    expect(readSidebarCookie('sidebar:state=true')).toBe(true);
  });

  it('treats a value it did not write as no value at all', () => {
    expect(readSidebarCookie('sidebar:state=yes')).toBeUndefined();
    expect(readSidebarCookie('sidebar:state=')).toBeUndefined();
    expect(readSidebarCookie('sidebar:state')).toBeUndefined();
  });

  it('does not answer for a cookie whose name merely ends in ours', () => {
    expect(readSidebarCookie('my-sidebar:state=true')).toBeUndefined();
  });
});

describe('resolveSidebarDefault', () => {
  it('follows the config while the user has expressed no preference', () => {
    expect(resolveSidebarDefault('', 'open')).toBe(true);
    expect(resolveSidebarDefault('', 'closed')).toBe(false);
    // "hidden" never reaches the provider — the sidebar is not mounted at all
    // — and an absent section is the built-in default, which is open.
    expect(resolveSidebarDefault('', undefined)).toBe(true);
  });

  it('prefers what the user last did to what the config would like', () => {
    expect(resolveSidebarDefault('sidebar:state=false', 'open')).toBe(false);
    expect(resolveSidebarDefault('sidebar:state=true', 'closed')).toBe(true);
  });
});
