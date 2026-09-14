import { snapshot_UNSTABLE } from 'recoil';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  rememberThreadId,
  sessionDescriptorState,
  sessionIdStorage
} from '../src/state';

/**
 * What a reload is allowed to inherit from the page that ran before it.
 *
 * The session id alone is not enough. A tab sitting on `/thread/:id` mounts
 * `AutoResumeThread`, which compares the thread in the URL against the
 * descriptor's and calls `clear()` when they differ -- on mount, before any
 * frame can answer. A restored descriptor with no thread therefore mints a
 * fresh session id and sends `session.clear` to the session the server had
 * just kept, taking its open question with it. So the thread the tab was in
 * travels with the id or the rescue never happens.
 */

const navigation = (type: string) => {
  vi.spyOn(performance, 'getEntriesByType').mockReturnValue([
    { type } as unknown as PerformanceEntry
  ]);
};

const threadKey = () => `${sessionIdStorage.key}:thread`;

const descriptor = () =>
  snapshot_UNSTABLE().getLoadable(sessionDescriptorState).getValue();

describe('the descriptor a reload comes back with', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('carries the stored thread as well as the stored session id', () => {
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    sessionStorage.setItem(threadKey(), 'thread-1');
    navigation('reload');

    expect(descriptor()).toEqual({
      sessionId: 'session-1',
      threadId: 'thread-1'
    });
  });

  it('carries the id alone when the tab was not in a thread yet', () => {
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    navigation('reload');

    expect(descriptor()).toEqual({ sessionId: 'session-1' });
  });

  it('inherits nothing on a navigation that is not a reload', () => {
    // A tab opened from this one inherits a copy of sessionStorage. Adopting
    // either value there would hijack the original tab's server session.
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    sessionStorage.setItem(threadKey(), 'thread-1');
    navigation('navigate');

    const restored = descriptor();
    expect(restored.sessionId).not.toBe('session-1');
    expect(restored.threadId).toBeUndefined();
    // And the thread is dropped, not merely unread: leaving it behind would
    // have the next reload of *this* tab resume the other tab's thread.
    expect(sessionStorage.getItem(threadKey())).toBeNull();
  });

  it('comes back with the thread the session moved into', () => {
    // The round trip, written the way the app writes it: the descriptor
    // never learns the thread of a chat begun at `/` -- the server names it
    // on `thread.first_interaction` -- so `currentThreadId` is what has to
    // be written down for the reload to have anything to carry.
    sessionStorage.setItem(sessionIdStorage.key, 'session-1');
    rememberThreadId('thread-9');
    navigation('reload');

    expect(descriptor()).toEqual({
      sessionId: 'session-1',
      threadId: 'thread-9'
    });
  });

  it('forgets the thread when the session leaves it', () => {
    // What `clear()` does on a new chat: a stale key would have the next
    // reload resume the conversation the user just walked away from.
    sessionStorage.setItem(threadKey(), 'thread-9');

    rememberThreadId(undefined);

    expect(sessionStorage.getItem(threadKey())).toBeNull();
  });
});
