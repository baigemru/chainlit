import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ServerMsg } from '@chainlit/react-client';

import ChatProfileSwitchListener from '@/components/ChatProfileSwitchListener';

/**
 * Taking a hand-off, through the one implementation that now serves both
 * callers.
 *
 * The switch logic moved out of `ChatProfileSwitchListener` into
 * `useSessionHandoff`, because the account page reaches the same place from
 * an `open_thread` action outcome. This spec drives the *listener*, so what
 * it guards is both halves at once: the subscription still forwards only
 * `session.handoff`, and the lifted body still refuses a no-op, adopts the
 * server's successor thread before navigating, and keeps a transcript when
 * asked to.
 */

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate
}));

const mockClear = vi.fn();
let chatProfile: string | undefined = 'fast';
let config: Record<string, unknown> = {};

// The transport's additive fan-out, as `ChatTransport.onMessage` does it.
let listeners: ((message: ServerMsg) => void)[] = [];
const transport = {
  onMessage: (listener: (message: ServerMsg) => void) => {
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((l) => l !== listener);
    };
  }
};

vi.mock('@chainlit/react-client', () => ({
  useChatTransport: () => transport,
  useConfig: () => ({ config }),
  useChatSession: () => ({ chatProfile }),
  useChatInteract: () => ({ clear: mockClear }),
  // Only ever handed to `useSetRecoilState`, which is answered by key below.
  acceptingState: { key: 'Accepting' },
  askUserState: { key: 'AskUser' },
  loadingState: { key: 'Loading' },
  messagesState: { key: 'Messages' }
}));

vi.mock('@/state/chat', () => ({
  attachmentsState: { key: 'Attachments' },
  chatBoundariesState: { key: 'ChatBoundaries' },
  collapsedExcursionsState: { key: 'CollapsedExcursions' },
  keptExcursionsState: { key: 'KeptExcursions' },
  openThreadTransitionState: { key: 'OpenThreadTransition' }
}));

// Freezing a live stream is `transcript.spec.tsx`'s subject; here it only has
// to hand the steps back so the kept-transcript branch has something to keep.
vi.mock('@/components/chat/MessagesContainer/transcript', () => ({
  freezeStreaming: (steps: unknown[]) => steps
}));

/** The atom values the hook reads back through its updaters. */
let store: Record<string, unknown> = {};

vi.mock('recoil', () => ({
  useSetRecoilState: (node: { key: string }) => (value: unknown) => {
    store[node.key] =
      typeof value === 'function'
        ? (value as (previous: unknown) => unknown)(store[node.key])
        : value;
  }
}));

const handoff = (over: Record<string, unknown> = {}): ServerMsg =>
  ({
    t: 'session.handoff',
    chatProfile: 'deep',
    nextThreadId: 'next-1',
    keepTranscript: false,
    hasTransitMessage: false,
    ...over
  }) as ServerMsg;

const deliver = (message: ServerMsg) => listeners.forEach((l) => l(message));

beforeEach(() => {
  vi.clearAllMocks();
  listeners = [];
  store = { Messages: [], ChatBoundaries: [] };
  chatProfile = 'fast';
  config = {
    chatProfiles: [{ name: 'fast' }, { name: 'deep' }],
    threadResumable: true
  };
});

describe('the hand-off the listener forwards', () => {
  it('adopts the successor thread the server minted and goes to it', () => {
    render(<ChatProfileSwitchListener />);
    deliver(handoff());

    expect(mockClear).toHaveBeenCalledWith({
      threadId: 'next-1',
      chatProfile: 'deep'
    });
    expect(mockNavigate).toHaveBeenCalledWith('/thread/next-1');
    expect(store.OpenThreadTransition).toEqual({
      threadId: 'next-1',
      keepTranscript: false
    });
  });

  it('ignores every other frame on the wire', () => {
    render(<ChatProfileSwitchListener />);
    // Shaped like a hand-off in everything but its tag: a subscription that
    // forwarded on anything else would switch the session on this.
    deliver({
      ...(handoff() as Record<string, unknown>),
      t: 'toast'
    } as unknown as ServerMsg);

    expect(mockClear).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('refuses a profile the config does not carry', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    render(<ChatProfileSwitchListener />);
    deliver(handoff({ chatProfile: 'nowhere' }));

    expect(mockClear).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('does nothing when the switch would change nothing', () => {
    render(<ChatProfileSwitchListener />);
    deliver(handoff({ chatProfile: 'fast' }));

    expect(mockClear).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('still switches within the profile when a transit message is parked', () => {
    // Leaving the record unclaimed would strand it -- this is the branch an
    // account action's `open_thread` outcome takes on the current profile.
    render(<ChatProfileSwitchListener />);
    deliver(handoff({ chatProfile: 'fast', hasTransitMessage: true }));

    expect(mockClear).toHaveBeenCalledWith({
      threadId: 'next-1',
      chatProfile: 'fast'
    });
  });

  it('keeps the transcript and draws the boundary on the last message', () => {
    store.Messages = [{ id: 'm-1' }, { id: 'm-2' }];
    store.ChatBoundaries = [{ afterMessageId: 'm-2', profile: 'old' }];

    render(<ChatProfileSwitchListener />);
    deliver(handoff({ keepTranscript: true }));

    expect(store.Messages).toEqual([{ id: 'm-1' }, { id: 'm-2' }]);
    // One boundary on that message, not two: the stale one is dropped rather
    // than left to have its divider silently overwritten.
    expect(store.ChatBoundaries).toEqual([
      { afterMessageId: 'm-2', profile: 'deep' }
    ]);
  });

  it('stays unnamed in an app that cannot resume a thread', () => {
    config = { chatProfiles: [{ name: 'fast' }, { name: 'deep' }] };

    render(<ChatProfileSwitchListener />);
    deliver(handoff());

    expect(mockNavigate).toHaveBeenCalledWith('/');
    expect(store.OpenThreadTransition).toBeUndefined();
  });
});
