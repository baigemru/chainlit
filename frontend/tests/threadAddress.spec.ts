import { describe, expect, it } from 'vitest';

// A relative path, not the `@/` alias: `tests/tsconfig.json` includes only
// `**/*.spec.tsx`, so `vite-tsconfig-paths` maps nothing for a `.spec.ts`.
import {
  threadAddressFor,
  threadIdFromLocation
} from '../src/lib/threadAddress';

/**
 * The two halves of "the address bar is the request": reading the thread out
 * of a location before the router exists, and deciding where the address has
 * to move once the server has named the thread it actually gave us.
 *
 * The reading half has to agree with react-router's own `stripBasename`
 * exactly. If it does not, the seeded descriptor and `useParams().id`
 * disagree on the first commit, and AutoResumeThread reads that as "this tab
 * asked for another thread" and clears the live session.
 */

describe('threadIdFromLocation', () => {
  const cases: [string, string, string | undefined][] = [
    ['/thread/t1', '', 't1'],
    ['/thread/t1/', '', 't1'],
    ['/thread/', '', undefined],
    ['/thread', '', undefined],
    ['/', '', undefined],
    ['', '', undefined],
    ['/share/t1', '', undefined],
    ['/element/e1', '', undefined],
    ['/login', '', undefined],
    ['/login/callback', '', undefined],
    ['/env', '', undefined],
    ['/anything/else', '', undefined],
    // Ids arrive percent-encoded when they are not plain uuids.
    ['/thread/a%20b', '', 'a b'],
    // A malformed escape must not throw on the way into RecoilRoot.
    ['/thread/a%zz', '', 'a%zz'],
    // Nothing below the thread route is the thread route.
    ['/thread/t1/extra', '', undefined],
    // basename, as react-router strips it: prefix match, case-insensitive,
    // and the next character has to be a boundary.
    ['/panda/thread/t1', '/panda', 't1'],
    ['/PANDA/thread/t1', '/panda', 't1'],
    ['/panda/', '/panda', undefined],
    ['/panda', '/panda', undefined],
    ['/pandax/thread/t1', '/panda', undefined],
    ['/thread/t1', '/panda', undefined],
    // A basename spelled with a trailing slash is supported by the router,
    // so it is supported here.
    ['/panda/thread/t1', '/panda/', 't1'],
    // '/' is the shape react-router normalises an empty basename to.
    ['/thread/t1', '/', 't1']
  ];

  it.each(cases)('%s under %s', (pathname, basename, expected) => {
    expect(threadIdFromLocation(pathname, basename)).toBe(expected);
  });
});

describe('threadAddressFor', () => {
  const cases: [string, string, string, string | null][] = [
    // A chat begun at `/` gets its address the moment the server names it.
    ['/', '', 't1', '/thread/t1'],
    ['/thread', '', 't1', '/thread/t1'],
    ['/thread/', '', 't1', '/thread/t1'],
    // The refused resume: the server moved the session, the address follows.
    ['/thread/other', '', 't1', '/thread/t1'],
    // Already there: no navigate, or every reconnect would push a history
    // entry and the Back button would stop working.
    ['/thread/t1', '', 't1', null],
    ['/thread/t1/', '', 't1', null],
    // Not chat routes. A read-only share or an element page must not be
    // yanked to the live conversation just because a socket connected.
    ['/share/t1', '', 't1', null],
    ['/share/other', '', 't1', null],
    ['/element/e1', '', 't1', null],
    ['/login', '', 't1', null],
    ['/login/callback', '', 't1', null],
    ['/env', '', 't1', null],
    ['/nonsense', '', 't1', null],
    // The pathname handed in is already basename-stripped by the router, so
    // the returned path is too -- `navigate` re-joins the basename itself.
    ['/thread/other', '/panda', 't1', null],
    ['/panda/thread/other', '/panda', 't1', '/thread/t1'],
    ['/panda/thread/t1', '/panda', 't1', null],
    ['/panda/', '/panda', 't1', '/thread/t1']
  ];

  it.each(cases)(
    '%s under %s -> %s',
    (pathname, basename, threadId, expected) => {
      expect(threadAddressFor(pathname, basename, threadId)).toBe(expected);
    }
  );

  it('encodes an id that is not url-safe', () => {
    // The round trip with threadIdFromLocation has to close, or the next
    // page load asks for a different thread than the one it is showing.
    const address = threadAddressFor('/', '', 'a b');
    expect(address).toBe('/thread/a%20b');
    expect(threadIdFromLocation(address!, '')).toBe('a b');
  });
});
