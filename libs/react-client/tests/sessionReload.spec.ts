import { snapshot_UNSTABLE } from 'recoil';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  currentThreadIdState,
  sessionDescriptorSeed,
  sessionDescriptorState,
  sessionIdStorage
} from '../src/state';

/**
 * What a reload is allowed to inherit from the page that ran before it.
 *
 * The session id comes out of sessionStorage; the thread does not, and may
 * not. A tab sitting on `/thread/:id` mounts `AutoResumeThread`, which
 * compares the thread in the URL against the descriptor's and calls
 * `clear()` when they differ -- on mount, before any frame can answer. A
 * descriptor that carried a *stored* thread could therefore disagree with
 * the address bar and wipe the session the server had just kept. So the
 * request is the address bar and nothing else, handed in by the host
 * through `sessionDescriptorSeed`: this package knows nothing about routes.
 */

const navigation = (type: string) => {
  vi.spyOn(performance, 'getEntriesByType').mockReturnValue([
    { type } as unknown as PerformanceEntry
  ]);
};

const legacyThreadKey = `${sessionIdStorage.key}:thread`;

const descriptor = () =>
  snapshot_UNSTABLE().getLoadable(sessionDescriptorState).getValue();

describe('the descriptor a page load comes back with', () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionDescriptorSeed.threadId = undefined;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    sessionDescriptorSeed.threadId = undefined;
  });

  it('restores the session id and asks for the thread the host names', () => {
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    sessionDescriptorSeed.threadId = () => 'thread-1';
    navigation('reload');

    expect(descriptor()).toEqual({
      sessionId: 'session-1',
      threadId: 'thread-1'
    });
  });

  it('asks for the host thread on a fresh id too', () => {
    // A new tab opened straight onto `/thread/:id`: the id is minted, but
    // the address still says which conversation the user asked for.
    sessionDescriptorSeed.threadId = () => 'thread-2';
    navigation('navigate');

    const restored = descriptor();
    expect(restored.sessionId).toBeTruthy();
    expect(restored.threadId).toBe('thread-2');
  });

  it('carries no thread when the host names none', () => {
    // The key is absent rather than present-and-undefined: the descriptor is
    // compared by shape in the transport's `sameSession`.
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    navigation('reload');

    expect(descriptor()).toEqual({ sessionId: 'session-1' });
  });

  it('inherits no session id on a navigation that is not a reload', () => {
    // A tab opened from this one inherits a copy of sessionStorage. Adopting
    // the id there would hijack the original tab's server session.
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    navigation('navigate');

    expect(descriptor().sessionId).not.toBe('session-1');
  });

  it('never reads or writes a thread of its own in storage', () => {
    // The second key is gone. A leftover from a version that had one must
    // not be adopted, and nothing here may recreate it -- a thread written
    // down beside the session id is a second answer to "which conversation
    // is this", and the address bar is the only one.
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    sessionStorage.setItem(legacyThreadKey, 'thread-stale');
    navigation('reload');

    expect(descriptor()).toEqual({ sessionId: 'session-1' });

    snapshot_UNSTABLE(({ set }) => {
      set(currentThreadIdState, 'thread-9');
    })
      .getLoadable(currentThreadIdState)
      .getValue();

    expect(sessionStorage.getItem(legacyThreadKey)).toBe('thread-stale');
  });
});
