import { act, render } from '@testing-library/react';
import { useEffect } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it } from 'vitest';

// The package's *sources*, for the reason `elementSidebarFrame.spec.tsx`
// gives: a `.ts` import goes through vite, which resolves react once.
import { ChatTransportContext } from '../../libs/react-client/src/context';
import type {
  ClientMsg,
  ServerMsg
} from '../../libs/react-client/src/protocol';
import type { SessionSink } from '../../libs/react-client/src/transport';
import { useChatData } from '../../libs/react-client/src/useChatData';
import { useChatSession } from '../../libs/react-client/src/useChatSession';

/**
 * `task.indicator` as the composer ends up reading it.
 *
 * Two booleans that used to be one. An application may declare a run
 * background (`cl.run_in_background`), and then the spinner is lit while the
 * person keeps typing into the same conversation — so "something is running"
 * and "it is your turn" travel separately, and this is the seam where they
 * stop agreeing. The real `useChatSession` and the real atoms, because what
 * is under test is that the frame reaches `useChatData.disabled` at all.
 */

const sent: ClientMsg[] = [];
let sink: SessionSink | undefined;

const snapshot = {
  phase: 'ready' as const,
  connected: true,
  error: false,
  superseded: false
};

const transport = {
  setSink: (next: SessionSink | undefined) => {
    sink = next;
  },
  attach: () => undefined,
  detach: () => undefined,
  send: (message: ClientMsg) => {
    sent.push(message);
  },
  subscribe: () => () => undefined,
  // One object, not a fresh one per call: `useChatData` reads this through
  // `useSyncExternalStore`, which compares snapshots by identity.
  getSnapshot: () => snapshot,
  onMessage: () => () => undefined
};

let chat: ReturnType<typeof useChatData>;

const Probe = () => {
  const { attach } = useChatSession();
  chat = useChatData();
  useEffect(() => {
    attach({});
  }, [attach]);
  return null;
};

const mount = () =>
  render(
    <RecoilRoot>
      <ChatTransportContext.Provider value={transport as never}>
        <Probe />
      </ChatTransportContext.Provider>
    </RecoilRoot>
  );

const deliver = (...frames: ServerMsg[]) =>
  act(() => {
    for (const frame of frames) sink!.onFrame(frame);
  });

beforeEach(() => {
  sent.length = 0;
  sink = undefined;
});

describe('the task indicator', () => {
  it('locks the composer for an ordinary turn', () => {
    mount();

    deliver({ t: 'task.indicator', running: true, accepting: false });

    expect(chat.loading).toBe(true);
    expect(chat.accepting).toBe(false);
    expect(chat.disabled).toBe(true);
  });

  it('leaves it open for a run the application declared background', () => {
    // The whole point: the spinner is lit, Stop is reachable, and the person
    // can still say "no, the other one" without having to stop the run to
    // say it.
    mount();

    deliver({ t: 'task.indicator', running: true, accepting: true });

    expect(chat.loading).toBe(true);
    expect(chat.disabled).toBe(false);
  });

  it('opens it again when the turn ends', () => {
    mount();
    deliver({ t: 'task.indicator', running: true, accepting: false });

    deliver({ t: 'task.indicator', running: false, accepting: true });

    expect(chat.loading).toBe(false);
    expect(chat.disabled).toBe(false);
  });

  it('reads an absent flag as open, never as locked', () => {
    // `accepting` is omitted from the frame when it is true
    // (`omit_defaults`), so the missing value is the permissive one. A
    // client that guessed the other way would lock the composer on every
    // frame the server thought it had nothing to say about.
    mount();
    deliver({ t: 'task.indicator', running: true, accepting: false });

    deliver({ t: 'task.indicator', running: true } as ServerMsg);

    expect(chat.accepting).toBe(true);
    expect(chat.disabled).toBe(false);
  });

  it('opens it when a question is asked, whatever was running', () => {
    mount();
    deliver({ t: 'task.indicator', running: true, accepting: false });

    deliver({
      t: 'ask.start',
      step: { id: 's1', type: 'assistant_message', threadId: 't1' },
      spec: { type: 'text', stepId: 's1', timeout: 60 }
    } as ServerMsg);

    expect(chat.loading).toBe(false);
    expect(chat.disabled).toBe(false);
  });
});
