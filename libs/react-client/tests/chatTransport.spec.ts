import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChainlitAPI } from '../src/api';
import type { ClientMsg, Hello, ServerMsg } from '../src/protocol';
import { ChatTransport } from '../src/transport';

/**
 * A WebSocket stand-in whose open/close/message are driven by the test.
 *
 * What is being tested here is not the socket — `chainlitSocket.spec.ts`
 * covers that — but who owns it: how many exist, which descriptor each one
 * announced, and whether an intent that was overtaken can still act.
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

/** A promise the test resolves by hand, so the cookie gap can be held open. */
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
};

/** Let every pending microtask (the awaits inside `attach`) run. */
const settle = async () => {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
};

let cookieCalls: string[];
let cookieGate: (() => Promise<void>) | undefined;

const client = {
  httpEndpoint: 'http://localhost:8000',
  type: 'webapp' as const,
  stickyCookie: (sessionId: string) => {
    cookieCalls.push(sessionId);
    return cookieGate ? cookieGate() : Promise.resolve({});
  }
} as unknown as ChainlitAPI;

const sockets = () => FakeWebSocket.instances;
const latest = () => sockets()[sockets().length - 1];

const ready = (sessionId: string): ServerMsg => ({
  t: 'session.ready',
  sessionId
});

/** Longer than the transport's own backoff ceiling. */
const BACKOFF_CEILING = 20_000;

describe('ChatTransport', () => {
  let transport: ChatTransport;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    cookieCalls = [];
    cookieGate = undefined;
    vi.stubGlobal('WebSocket', FakeWebSocket);
    transport = new ChatTransport(client);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opens one connection for a descriptor, however often it is attached', async () => {
    transport.attach({ sessionId: 'a' });
    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));

    transport.attach({ sessionId: 'a' });
    await settle();

    expect(sockets()).toHaveLength(1);
    expect(cookieCalls).toEqual(['a']);
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

  it('closes the old connection and opens exactly one for a new descriptor', async () => {
    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));
    const first = latest();

    transport.attach({ sessionId: 'b' });
    await settle();

    expect(sockets()).toHaveLength(2);
    expect(first.closedByOwner).toBe(true);
    expect(latest()).not.toBe(first);
    latest().open();
    expect(
      latest()
        .frames()
        .filter((frame) => frame.t === 'hello')
    ).toHaveLength(1);
    expect(latest().hello().sessionId).toBe('b');
  });

  it('rebuilds for the same session on a new thread, as the copilot does', async () => {
    transport.attach({ sessionId: 'a', threadId: 'one' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));

    transport.attach({ sessionId: 'a', threadId: 'two' });
    await settle();
    latest().open();

    expect(sockets()).toHaveLength(2);
    expect(latest().hello()).toMatchObject({ sessionId: 'a', threadId: 'two' });
  });

  it('lets an attach overtaken during the cookie call neither open nor close', async () => {
    const first = deferred();
    const second = deferred();
    const gates = [first, second];
    cookieGate = () => gates.shift()!.promise;

    transport.attach({ sessionId: 'a' });
    // The session is abandoned while its cookie is still in flight -- the
    // race the `openForRef` mirror used to lose, handing the abandoned
    // session a socket and the live one a close.
    transport.attach({ sessionId: 'b' });

    second.resolve();
    await settle();
    latest().open();
    latest().deliver(ready('b'));
    const live = latest();

    first.resolve();
    await settle();

    expect(sockets()).toHaveLength(1);
    expect(live.closedByOwner).toBe(false);
    expect(live.hello().sessionId).toBe('b');
    expect(transport.getSnapshot().connected).toBe(true);
  });

  it('lets a detach during the cookie call cancel the connection outright', async () => {
    const gate = deferred();
    cookieGate = () => gate.promise;

    transport.attach({ sessionId: 'a' });
    transport.detach();
    gate.resolve();
    await settle();

    expect(sockets()).toHaveLength(0);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'idle',
      connected: false
    });
  });

  it('keeps queued work across a rebuild and flushes it on session.ready', async () => {
    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    // Queued before the handshake finished, so it is still in the buffer
    // when the descriptor changes under it.
    transport.send({
      t: 'ask.reply',
      stepId: 'step-1',
      value: { kind: 'file' }
    });
    expect(transport.sendBuffer).toHaveLength(1);

    transport.attach({ sessionId: 'b' });
    await settle();
    expect(transport.sendBuffer).toHaveLength(1);

    latest().open();
    latest().deliver(ready('b'));

    expect(transport.sendBuffer).toHaveLength(0);
    expect(
      latest()
        .frames()
        .map((frame) => frame.t)
    ).toEqual(['hello', 'ask.reply']);
  });

  it('stays superseded after close 4409 until another descriptor arrives', async () => {
    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));

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
    transport.attach({ sessionId: 'a' });
    await settle();
    vi.advanceTimersByTime(BACKOFF_CEILING * 10);
    expect(sockets()).toHaveLength(1);

    transport.attach({ sessionId: 'b' });
    await settle();
    expect(sockets()).toHaveLength(2);
    expect(transport.getSnapshot().superseded).toBe(false);
  });

  it('reopens the same descriptor once the transport has given up', async () => {
    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    // 4401: no credentials. Terminal, so the socket stops trying -- and a
    // fresh attach after the user logs in is what brings it back.
    latest().drop(4401);
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'closed',
      error: true
    });

    transport.attach({ sessionId: 'a' });
    await settle();
    expect(sockets()).toHaveLength(2);
  });

  it('heals a transport blip by itself, without a new attach', async () => {
    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));

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
    latest().deliver(ready('a'));
    expect(transport.getSnapshot()).toMatchObject({
      phase: 'ready',
      connected: true
    });
  });

  it('carries a new chat profile into the next handshake without reconnecting', async () => {
    transport.attach({ sessionId: 'a' }, { chatProfile: 'first' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));
    expect(latest().hello().chatProfile).toBe('first');

    // What `session.ready` and `thread.resume` do: the server names a
    // profile the client did not offer.
    transport.attach({ sessionId: 'a' }, { chatProfile: 'second' });
    await settle();
    expect(sockets()).toHaveLength(1);

    latest().drop();
    vi.advanceTimersByTime(BACKOFF_CEILING);
    latest().open();
    expect(latest().hello().chatProfile).toBe('second');
  });

  it('offers the thread the session is in over the one it was opened to resume', async () => {
    transport.attach(
      { sessionId: 'a', threadId: 'resumed' },
      { threadId: 'moved-on' }
    );
    await settle();
    latest().open();

    expect(latest().hello().threadId).toBe('moved-on');
  });

  it('delivers every frame to the sink first and then to the listeners', async () => {
    const seen: string[] = [];
    transport.setSink({
      onFrame: (message) => seen.push(`sink:${message.t}`),
      onClose: () => undefined
    });
    transport.onMessage((message) => seen.push(`listener:${message.t}`));

    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));
    latest().deliver({ t: 'toast', message: 'hi' });

    expect(seen).toEqual([
      'sink:session.ready',
      'listener:session.ready',
      'sink:toast',
      'listener:toast'
    ]);
  });

  it('keeps its listeners across a rebuild', async () => {
    const seen: string[] = [];
    transport.onMessage((message) => seen.push(message.t));

    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().deliver(ready('a'));

    transport.attach({ sessionId: 'b' });
    await settle();
    latest().open();
    latest().deliver(ready('b'));

    expect(seen).toEqual(['session.ready', 'session.ready']);
  });

  it('tells the sink about a close so the refused session id can be replaced', async () => {
    const closes: number[] = [];
    transport.setSink({
      onFrame: () => undefined,
      onClose: (info) => closes.push(info.code)
    });

    transport.attach({ sessionId: 'a' });
    await settle();
    latest().open();
    latest().drop(4403);

    expect(closes).toEqual([4403]);
  });
});
