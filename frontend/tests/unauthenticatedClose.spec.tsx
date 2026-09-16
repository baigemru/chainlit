import { act, render } from '@testing-library/react';
import { useEffect } from 'react';
import { RecoilRoot, useRecoilValue } from 'recoil';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Everything below is the package's *sources*, not its bundle -- see the
// header of `elementSidebarFrame.spec.tsx`: the built `.mjs` externalises
// `react` and resolves it to the copy under `libs/react-client`, a second
// React whose hook dispatcher is null under this react-dom.
import { ChatTransportContext } from '../../libs/react-client/src/context';
import type { ServerMsg } from '../../libs/react-client/src/protocol';
import { userState } from '../../libs/react-client/src/state';
import { ChatTransport } from '../../libs/react-client/src/transport';
import type { IUser } from '../../libs/react-client/src/types';
import { useChatSession } from '../../libs/react-client/src/useChatSession';

/**
 * What a close 4401 does to the signed-in user.
 *
 * The whole chain, not the sink alone: a real `ChatTransport` over a stubbed
 * `WebSocket`, so the assertion covers the socket's terminal rule, the
 * transport's close branch and the `useChatSession` sink together. That link
 * is the fix -- the tab used to sit on "reconnecting" forever while the
 * cookie behind it had already expired.
 */

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }

  // --- test drivers -------------------------------------------------------

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  deliver(message: ServerMsg): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop(code = 1006, reason = ''): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

const latest = () =>
  FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

const someone: IUser = {
  id: 'u-1',
  identifier: 'someone@example.com',
  metadata: {}
};

let transport: ChatTransport;
/** What the store says about the user, read out of the tree on every render. */
let user: IUser | null | undefined;

const Probe = () => {
  const { attach } = useChatSession();
  user = useRecoilValue(userState);
  useEffect(() => {
    attach({});
  }, [attach]);
  return null;
};

const mount = () =>
  render(
    <RecoilRoot initializeState={({ set }) => set(userState, someone)}>
      <ChatTransportContext.Provider value={transport}>
        <Probe />
      </ChatTransportContext.Provider>
    </RecoilRoot>
  );

/** Open the socket and finish the handshake, the way a live session starts. */
const connect = () =>
  act(() => {
    latest().open();
    latest().deliver({ t: 'session.ready', sessionId: 's1' });
  });

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  user = undefined;
  vi.stubGlobal('WebSocket', FakeWebSocket);
  // A fresh one per test: `chatTransportFor` hands out a singleton per
  // endpoint, and a transport kept across tests would carry the previous
  // one's descriptor and its generation.
  transport = new ChatTransport({
    httpEndpoint: 'http://localhost:8000',
    type: 'webapp'
  } as never);
});

afterEach(() => {
  // Before the stub comes off: a pending retry that fires afterwards would
  // reach jsdom's real WebSocket and dial localhost from the test run.
  transport.detach();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('a socket closed 4401', () => {
  it('forgets the user, the way an HTTP 401 does', () => {
    mount();
    connect();
    expect(user).toEqual(someone);

    act(() => latest().drop(4401));

    // `useAuth` reads `isAuthenticated: !!user`, so this is what shuts the
    // attach effect and sends the page to /login. `null`, not `undefined`:
    // `isReady` distinguishes "asked and nobody is signed in" from "have not
    // asked yet", and a tab that reconnects forever is the second one.
    expect(user).toBeNull();
  });

  it('leaves the user alone when the connection merely dropped', () => {
    // 1006 is a network blip the transport heals by itself. Signing the user
    // out of a chat because a wifi handover took two seconds is the bug this
    // control guards against.
    mount();
    connect();

    act(() => latest().drop(1006));

    expect(user).toEqual(someone);
  });
});
