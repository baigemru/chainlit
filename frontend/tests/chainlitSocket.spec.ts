import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  ClientMsg,
  Hello,
  ServerMsg
} from '../../libs/react-client/src/protocol';
import {
  ChainlitSocket,
  websocketUrl
} from '../../libs/react-client/src/socket';

/**
 * A WebSocket stand-in whose open/close/message are driven by the test.
 *
 * The transport is the one piece of this client that cannot be exercised
 * against a real server yet, and it is also the piece whose failure mode is
 * silent: a buffer that flushes half a beat early delivers work to a session
 * that is not ready for it, and nothing in the UI says so.
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

  frames(): ClientMsg[] {
    return this.sent.map((raw) => JSON.parse(raw) as ClientMsg);
  }
}

const HELLO: Hello = { t: 'hello', sessionId: 'session-1' };

const READY: ServerMsg = { t: 'session.ready', sessionId: 'session-1' };

const latest = () =>
  FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

let received: ServerMsg[];
let closes: { code: number; opened: boolean; terminal: boolean }[];

const build = () => {
  const socket = new ChainlitSocket({
    url: 'ws://localhost:8000/ws',
    hello: () => HELLO,
    onClose: ({ code, opened, terminal }) =>
      closes.push({ code, opened, terminal })
  });
  socket.subscribe((message) => received.push(message));
  return socket;
};

describe('ChainlitSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    received = [];
    closes = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('sends hello on open and nothing else until session.ready', () => {
    const socket = build();
    socket.connect();
    latest().open();

    expect(latest().frames()).toEqual([HELLO]);

    socket.send({ t: 'stop' });
    expect(socket.sendBuffer).toHaveLength(1);
    expect(latest().frames()).toEqual([HELLO]);

    latest().deliver(READY);
    expect(socket.sendBuffer).toHaveLength(0);
    expect(latest().frames()).toEqual([HELLO, { t: 'stop' }]);
    expect(socket.connected).toBe(true);
  });

  it('drains the buffer in the order the sends were made', () => {
    const socket = build();
    socket.connect();
    latest().open();

    socket.send({ t: 'ask.reply', stepId: 'a', value: { kind: 'file' } });
    socket.send({ t: 'ask.reply', stepId: 'b', value: { kind: 'file' } });
    socket.send({ t: 'stop' });

    latest().deliver(READY);

    expect(
      latest()
        .frames()
        .map((frame) => frame.t)
    ).toEqual(['hello', 'ask.reply', 'ask.reply', 'stop']);
    expect(
      latest()
        .frames()
        .filter(
          (frame): frame is Extract<ClientMsg, { t: 'ask.reply' }> =>
            frame.t === 'ask.reply'
        )
        .map((frame) => frame.stepId)
    ).toEqual(['a', 'b']);
  });

  it('keeps a reply made while the transport is down and delivers it after the reconnect', () => {
    const socket = build();
    socket.connect();
    latest().open();
    latest().deliver(READY);

    latest().drop();
    expect(socket.connected).toBe(false);

    // The click happens with no socket at all.
    socket.send({ t: 'ask.reply', stepId: 'step-1', value: { kind: 'file' } });
    expect(socket.sendBuffer).toHaveLength(1);

    vi.advanceTimersByTime(BACKOFF_CEILING);
    latest().open();
    // Still buffered: the handshake is not complete yet.
    expect(socket.sendBuffer).toHaveLength(1);

    latest().deliver(READY);
    expect(socket.sendBuffer).toHaveLength(0);
    expect(
      latest()
        .frames()
        .map((frame) => frame.t)
    ).toEqual(['hello', 'ask.reply']);
  });

  it('appends a message sent from a session.ready listener behind the buffered ones', () => {
    const socket = build();
    socket.connect();
    latest().open();
    socket.send({ t: 'ask.reply', stepId: 'first', value: { kind: 'file' } });
    socket.subscribe((message) => {
      if (message.t === 'session.ready') socket.send({ t: 'stop' });
    });

    latest().deliver(READY);

    expect(
      latest()
        .frames()
        .map((frame) => frame.t)
    ).toEqual(['hello', 'ask.reply', 'stop']);
  });

  it('answers a heartbeat immediately and never buffers the ack', () => {
    const socket = build();
    socket.connect();
    latest().open();

    // Before session.ready: the ack still goes out, unbuffered.
    latest().deliver({ t: 'hb', seq: 7 });
    expect(socket.sendBuffer).toHaveLength(0);
    expect(latest().frames()).toEqual([HELLO, { t: 'hb.ack', seq: 7 }]);
  });

  it('reconnects with a growing delay and stops on a terminal close code', () => {
    const socket = build();
    socket.connect();
    latest().open();
    latest().deliver(READY);

    latest().drop(4408); // heartbeat timeout — retried
    expect(closes.at(-1)).toMatchObject({ terminal: false });
    vi.advanceTimersByTime(BACKOFF_CEILING);
    expect(FakeWebSocket.instances).toHaveLength(2);

    latest().drop(4409); // superseded — another connection owns the session
    expect(closes.at(-1)).toMatchObject({ code: 4409, terminal: true });
    vi.advanceTimersByTime(BACKOFF_CEILING * 10);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it('reports a refused upgrade as a close that never opened', () => {
    const socket = build();
    socket.connect();
    // A guard denies before accept(): the browser sees the HTTP handshake
    // fail and reports an abnormal closure with no close frame behind it.
    latest().drop(1006);

    expect(closes).toEqual([{ code: 1006, opened: false, terminal: false }]);
    // Indistinguishable from an unreachable server, so it is still retried.
    vi.advanceTimersByTime(BACKOFF_CEILING);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(socket.connected).toBe(false);
  });

  it('does not reconnect after a deliberate close', () => {
    const socket = build();
    socket.connect();
    latest().open();
    latest().deliver(READY);

    socket.close();
    vi.advanceTimersByTime(BACKOFF_CEILING * 10);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('drops a socket that stopped sending heartbeats', () => {
    const socket = build();
    socket.connect();
    latest().open();
    latest().deliver({
      t: 'session.ready',
      sessionId: 's',
      heartbeatIntervalMs: 20_000
    });

    // A half-open socket never fires close on its own; the watchdog does.
    vi.advanceTimersByTime(60_000);
    expect(socket.connected).toBe(false);
    expect(closes.at(-1)).toMatchObject({ terminal: false });
  });

  it('gives up on a server that opens the socket but never answers hello', () => {
    const socket = build();
    socket.connect();
    latest().open();
    // No session.ready, and no close either: a stuck handler behind an
    // accepted upgrade produces silence, not an error.
    vi.advanceTimersByTime(60_000);

    expect(closes.at(-1)).toMatchObject({ opened: true, terminal: false });
    vi.advanceTimersByTime(BACKOFF_CEILING);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it('forwards every message to every subscriber, in arrival order', () => {
    const socket = build();
    socket.connect();
    latest().open();
    latest().deliver(READY);
    latest().deliver({ t: 'task.indicator', running: true });
    latest().deliver({ t: 'toast', message: 'hi' });

    expect(received.map((message) => message.t)).toEqual([
      'session.ready',
      'task.indicator',
      'toast'
    ]);
  });
});

describe('websocketUrl', () => {
  it('keeps the server root path and follows the scheme', () => {
    expect(websocketUrl('http://localhost:8000')).toBe(
      'ws://localhost:8000/ws'
    );
    expect(websocketUrl('https://example.com/chat/')).toBe(
      'wss://example.com/chat/ws'
    );
  });
});

/** Longer than the transport's own backoff ceiling. */
const BACKOFF_CEILING = 20_000;
