import { snapshot_UNSTABLE } from 'recoil';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { sessionDescriptorSeed, sessionDescriptorState } from '../src/state';

/**
 * What a page load is allowed to inherit from the page that ran before it:
 * nothing.
 *
 * The thread comes out of the address bar, handed in by the host through
 * `sessionDescriptorSeed` — this package knows nothing about routes. There
 * is no second place a conversation can be named from any more: the session
 * id is minted by the server, so there is nothing to write down per tab and
 * nothing a duplicated tab (which inherits a copy of sessionStorage) could
 * copy in order to claim a live session. A second tab on the same address is
 * a takeover the server decides on.
 *
 * This replaces `sessionReload.spec.ts`, which pinned the reverse rule: an
 * id restored from sessionStorage, but only on a Navigation Timing `reload`.
 */

const descriptor = () =>
  snapshot_UNSTABLE().getLoadable(sessionDescriptorState).getValue();

describe('the descriptor a page load comes back with', () => {
  let storageReads: string[];
  let storageWrites: string[];

  beforeEach(() => {
    sessionDescriptorSeed.threadId = undefined;
    storageReads = [];
    storageWrites = [];
    // Stubbed whole rather than spied on through `Storage.prototype`:
    // jsdom's storages carry their own methods, so a prototype spy records
    // nothing and the assertion below would pass against any amount of
    // reading and writing (verified: it did).
    const recorder = (name: string) => ({
      getItem: (key: string) => {
        storageReads.push(`${name}:${key}`);
        return null;
      },
      setItem: (key: string) => {
        storageWrites.push(`${name}:${key}`);
      },
      removeItem: () => undefined,
      clear: () => undefined
    });
    vi.stubGlobal('sessionStorage', recorder('session'));
    vi.stubGlobal('localStorage', recorder('local'));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionDescriptorSeed.threadId = undefined;
  });

  it('asks for the thread the host names', () => {
    sessionDescriptorSeed.threadId = () => 'thread-1';

    expect(descriptor()).toEqual({ threadId: 'thread-1' });
  });

  it('carries no thread when the host names none', () => {
    // The key is absent rather than present-and-undefined: the descriptor is
    // compared by shape in the transport's `sameSession`.
    expect(descriptor()).toEqual({});
  });

  it('touches no browser storage on either branch', () => {
    sessionDescriptorSeed.threadId = () => 'thread-1';
    descriptor();
    sessionDescriptorSeed.threadId = undefined;
    descriptor();

    expect(storageReads).toEqual([]);
    expect(storageWrites).toEqual([]);
  });
});
