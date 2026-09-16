import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChainlitAPI } from '../src/api';
import type { ClientMsg, Hello, ServerMsg } from '../src/protocol';
import { ChatTransport } from '../src/transport';

/**
 * A WebSocket stand-in whose open/close/message are driven by the test.
 *
 * What is being tested here is not the socket — `chainlitSocket.spec.ts`
 * covers that — but who owns it: how many exist, which conversation each one
 * announced, and whether a connection that was replaced can still act.
 */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  closedByOwner = false;

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
    this.closedByOwner = true;
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

  frames(): ClientMsg[] {
    return this.sent.map((raw) => JSON.parse(raw) as ClientMsg);
  }

  hello(): Hello {
    return this.frames()[0] as Hello;
  }
}

/**
 * Every HTTP call the transport could make on its way to a socket.
 *
 * There used to be one — `POST /set-session-cookie`, awaited before the
 * socket could open. It is gone with the client-minted session id, and the
 * stub keeps a method for it so a test can prove nothing calls it.
 */
let httpCalls: string[];

const client = {
  httpEndpoint: 'http://localhost:8000',
  type: 'webapp' as const,
  stickyCookie: (sessionId: string) => {
    httpCalls.push(`stickyCookie:${sessionId}`);
    return Promise.resolve({});
  }
} as unknown as ChainlitAPI;

const sockets = () => FakeWebSocket.instances;
const latest = () => sockets()[sockets().length - 1];

const ready = (sessionId = 'server-minted', threadId?: string): ServerMsg => ({
  t: 'session.ready',
  sessionId,
  threadId
});

/** Longer than the transport's own backoff ceiling. */
const BACKOFF_CEILING = 20_000;

describe('ChatTransport', () => {
  let transport: ChatTransport;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    httpCalls = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    transport = new ChatTransport(client);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opens one connection for a thread, however often it is attached', () => {
    transport.attach({ threadId: 'one' });
    transport.attach({ threadId: 'one' });
    latest().open();
    latest().deliver(ready());

    transport.attach({ threadId: 'one' });

    expect(sockets()).toHaveLength(1);
    expect(
      latest()
        .frames()
        .filter((frame) => frame.t === 'hello')
    ).toHaveLength(1);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'ready',
      connected: true
    });
  });

  it('opens without waiting for anything over HTTP first', () => {
    // The sticky cookie is gone: the id it pinned a worker with is minted by
    // the server now, so there is nothing to pin before the handshake. A
    // synchronous socket is the whole claim — an awaited call here is a
    // window in which a second navigation can overtake the first.
    transport.attach({ threadId: 'one' });

    expect(sockets()).toHaveLength(1);
    expect(httpCalls).toEqual([]);
  });

  it('names the thread and nothing else in the handshake', () => {
    // The identity on the wire is the thread. A session id in `hello` is
    // what let a duplicated tab claim a live session by copying a string.
    transport.attach({ threadId: 'one' });
    latest().open();

    expect(latest().hello()).toMatchObject({ t: 'hello', threadId: 'one' });
    expect('sessionId' in latest().hello()).toBe(false);
  });

  it('closes the old connection and opens exactly one for a new thread', () => {
    transport.attach({ threadId: 'one' });
    latest().open();
    latest().deliver(ready());
    const first = latest();

    transport.attach({ threadId: 'two' });

    expect(sockets()).toHaveLength(2);
    expect(first.closedByOwner).toBe(true);
    expect(latest()).not.toBe(first);
    latest().open();
    expect(
      latest()
        .frames()
        .filter((frame) => frame.t === 'hello')
    ).toHaveLength(1);
    expect(latest().hello().threadId).toBe('two');
  });

  it('opens a second connection for a second clear, with no thread either side', () => {
    // Two "New chat" clicks in a row. Neither descriptor names a thread, so
    // `sameSession` cannot tell them apart — `detach()` is what does, by
    // forgetting the descriptor, and this is the pin on that. Without it the
    // second click would re-state an identity the transport thinks it is
    // already attached to and quietly keep the abandoned socket.
    transport.attach({});
    latest().open();
    latest().deliver(ready());

    transport.detach();
    transport.attach({});
    latest().open();
    latest().deliver(ready());

    transport.detach();
    transport.attach({});

    expect(sockets()).toHaveLength(3);
    expect(transport.getSnapshot().phase).toBe('connecting');
  });

  it('lets a detach cancel a connection that has not opened yet', () => {
    transport.attach({ threadId: 'one' });
    transport.detach();

    expect(sockets()[0].closedByOwner).toBe(true);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'idle',
      connected: false
    });
  });

  it('lets a detach cancel a retry that has not fired yet', () => {
    // The blip the transport was about to heal by itself, interrupted by the
    // user navigating away. A pending retry that survives the detach opens a
    // connection for a conversation nobody is in any more -- and with the
    // generation fence swallowing its status, it opens invisibly.
    transport.attach({ threadId: 'one' });
    latest().open();
    latest().deliver(ready());
    latest().drop();
    expect(transport.getSnapshot().phase).toBe('reconnecting');

    transport.detach();
    vi.advanceTimersByTime(BACKOFF_CEILING * 10);

    expect(sockets()).toHaveLength(1);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'idle',
      connected: false
    });
  });

  it('keeps queued work across a rebuild and flushes it on session.ready', () => {
    transport.attach({ threadId: 'one' });
    latest().open();
    // Queued before the handshake finished, so it is still in the buffer
    // when the descriptor changes under it.
    transport.send({
      t: 'ask.reply',
      stepId: 'step-1',
      value: { kind: 'file' }
    });
    expect(transport.sendBuffer).toHaveLength(1);

    transport.attach({ threadId: 'two' });
    expect(transport.sendBuffer).toHaveLength(1);

    latest().open();
    latest().deliver(ready());

    expect(transport.sendBuffer).toHaveLength(0);
    expect(
      latest()
        .frames()
        .map((frame) => frame.t)
    ).toEqual(['hello', 'ask.reply']);
  });

  it('stays superseded after close 4409 until another descriptor arrives', () => {
    transport.attach({ threadId: 'one' });
    latest().open();
    latest().deliver(ready());

    latest().drop(4409);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'superseded',
      connected: false,
      // Not an error: the conversation is being had elsewhere, it did not
      // fail. The resume toast and the redirect home hang off `error`.
      error: false,
      superseded: true
    });

    // Re-stating the same intent must not see-saw the session back.
    transport.attach({ threadId: 'one' });
    vi.advanceTimersByTime(BACKOFF_CEILING * 10);
    expect(sockets()).toHaveLength(1);

    transport.attach({ threadId: 'two' });
    expect(sockets()).toHaveLength(2);
    expect(transport.getSnapshot().superseded).toBe(false);
  });

  it('retries a 4403 instead of giving up on it', () => {
    // 4403 and 4404 were terminal while the client named the session and the
    // thread it wanted and the server could refuse either. It names no
    // session any more and a thread it may not have is answered with a fresh
    // one, so neither code is sent — and a private-range code nobody defines
    // must be read as an ordinary drop, not as a reason to stop trying.
    transport.attach({ threadId: 'one' });
    latest().open();
    latest().drop(4403);

    expect(transport.getSnapshot().phase).toBe('reconnecting');
    vi.advanceTimersByTime(BACKOFF_CEILING);
    expect(sockets()).toHaveLength(2);
  });

  it('reopens the same descriptor once the transport has given up', () => {
    transport.attach({ threadId: 'one' });
    latest().open();
    // 4401: no credentials. Terminal, so the socket stops trying -- and a
    // fresh attach after the user logs in is what brings it back.
    latest().drop(4401);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'closed',
      error: true
    });

    transport.attach({ threadId: 'one' });
    expect(sockets()).toHaveLength(2);
  });

  it('heals a transport blip by itself, without a new attach', () => {
    transport.attach({ threadId: 'one' });
    latest().open();
    latest().deliver(ready());

    latest().drop();
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'reconnecting',
      connected: false,
      // A drop the transport is about to heal is not the user's business.
      error: false
    });

    vi.advanceTimersByTime(BACKOFF_CEILING);
    expect(sockets()).toHaveLength(2);
    latest().open();
    latest().deliver(ready());
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'ready',
      connected: true
    });
  });

  it('carries a new chat profile into the next handshake without reconnecting', () => {
    transport.attach({ threadId: 'one' }, { chatProfile: 'first' });
    latest().open();
    latest().deliver(ready());
    expect(latest().hello().chatProfile).toBe('first');

    // What `session.ready` and `thread.resume` do: the server names a
    // profile the client did not offer.
    transport.attach({ threadId: 'one' }, { chatProfile: 'second' });
    expect(sockets()).toHaveLength(1);

    latest().drop();
    vi.advanceTimersByTime(BACKOFF_CEILING);
    latest().open();
    expect(latest().hello().chatProfile).toBe('second');
  });

  it('reports the device in the handshake without making it an identity', () => {
    transport.attach({ threadId: 'one' }, { device: 'mobile' });
    latest().open();
    latest().deliver(ready());
    expect(latest().hello().device).toBe('mobile');

    // A rotation past the breakpoint is a payload refresh, not a different
    // conversation: the socket must survive it.
    transport.attach({ threadId: 'one' }, { device: 'pc' });
    expect(sockets()).toHaveLength(1);

    latest().drop();
    vi.advanceTimersByTime(BACKOFF_CEILING);
    latest().open();
    expect(latest().hello().device).toBe('pc');
  });

  it('leaves the device out of a handshake nobody stated one for', () => {
    transport.attach({});
    latest().open();

    expect(latest().hello().device).toBeUndefined();
  });

  it('offers the thread the session is in over the one it was opened to resume', () => {
    transport.attach({ threadId: 'resumed' }, { threadId: 'moved-on' });
    latest().open();

    expect(latest().hello().threadId).toBe('moved-on');
  });

  it('delivers every frame to the sink first and then to the listeners', () => {
    const seen: string[] = [];
    transport.setSink({
      onFrame: (message) => seen.push(`sink:${message.t}`),
      onClose: () => undefined
    });
    transport.onMessage((message) => seen.push(`listener:${message.t}`));

    transport.attach({});
    latest().open();
    latest().deliver(ready());
    latest().deliver({ t: 'toast', message: 'hi' });

    expect(seen).toEqual([
      'sink:session.ready',
      'listener:session.ready',
      'sink:toast',
      'listener:toast'
    ]);
  });

  it('keeps its listeners across a rebuild', () => {
    const seen: string[] = [];
    transport.onMessage((message) => seen.push(message.t));

    transport.attach({ threadId: 'one' });
    latest().open();
    latest().deliver(ready());

    transport.attach({ threadId: 'two' });
    latest().open();
    latest().deliver(ready());

    expect(seen).toEqual(['session.ready', 'session.ready']);
  });

  it('tells the sink about a close so a refusal can be acted on', () => {
    const closes: number[] = [];
    transport.setSink({
      onFrame: () => undefined,
      onClose: (info) => closes.push(info.code)
    });

    transport.attach({ threadId: 'one' });
    latest().open();
    latest().drop(4401);

    expect(closes).toEqual([4401]);
  });
});
