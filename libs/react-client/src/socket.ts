/**
 * The websocket transport.
 *
 * Replaces socket.io. Of everything socket.io provided, exactly two things
 * are reproduced here, because the application genuinely depends on them:
 *
 * 1. **Automatic reconnection with backoff.** No client code anywhere
 *    reconnects by hand — there is no disconnect handler in the whole app.
 * 2. **An outbound buffer that survives the gap.** `ask.reply` is a plain
 *    message rather than a request/response ack precisely so it can sit in
 *    this buffer across a reconnect; a click made during a network blip is
 *    delivered when the socket comes back.
 *
 * Rooms, long-poll fallback, ack callbacks and event-name multiplexing are
 * dropped with no replacement. Every frame is JSON in a text frame; there is
 * no binary branch.
 */
import type { ClientMsg, Hello, ServerMsg } from './protocol';
import { CloseCode } from './protocol';

/** Path the websocket route is mounted on, appended to the server root path. */
export const WEBSOCKET_PATH = '/ws';

/** First retry delay. Doubles per consecutive failure. */
const BACKOFF_BASE_MS = 300;
/** Ceiling for the retry delay. */
const BACKOFF_MAX_MS = 10_000;
/** Fallback heartbeat interval when `session.ready` does not state one. */
const DEFAULT_HEARTBEAT_MS = 20_000;
/** Multiple of the heartbeat interval after which a silent socket is dropped. */
const HEARTBEAT_GRACE = 2.5;
/** Floor for the silence watchdog, so a tiny interval cannot thrash. */
const MIN_WATCHDOG_MS = 10_000;

/**
 * Close codes the transport will not retry on its own. Every one of them
 * repeats identically on a fresh connection, so retrying is a busy loop:
 * a malformed handshake and an oversized frame are client bugs, missing or
 * insufficient credentials need the user, and a superseded session belongs
 * to another connection now — reconnecting would see-saw it between tabs.
 * `onClose` still fires, so the application can act (see the one-shot
 * session-id reset in `useChatSession`).
 *
 * `BACKLOG_EXCEEDED` is deliberately absent: it means the server gave up
 * waiting for us to read, and reconnecting is the whole recovery — the
 * resume rebuilds the view a dropped delta would have corrupted.
 */
const TERMINAL_CLOSE_CODES: ReadonlySet<number> = new Set<number>([
  CloseCode.BAD_HANDSHAKE,
  CloseCode.UNAUTHENTICATED,
  CloseCode.SESSION_FORBIDDEN,
  CloseCode.THREAD_FORBIDDEN,
  CloseCode.SUPERSEDED,
  CloseCode.FRAME_TOO_LARGE
]);

/**
 * Messages that are never buffered.
 *
 * `hb.ack` answers a specific probe on a specific connection — a stale ack
 * redelivered after a reconnect is noise. `session.clear` says "drop this
 * session instead of keeping it warm"; buffering it across a reconnect would
 * kill the very session the reconnect just rescued. (`hello` never reaches
 * `send` at all: the transport writes it itself on every open.)
 */
const LIVE_ONLY: ReadonlySet<ClientMsg['t']> = new Set<ClientMsg['t']>([
  'hb.ack',
  'session.clear'
]);

export type ConnectionStatus = 'connecting' | 'ready' | 'closed';

export interface CloseInfo {
  /** Websocket close code. 1006 when the handshake itself failed. */
  code: number;
  reason: string;
  /**
   * False when `onopen` never fired. A guard denial happens before the
   * server accepts the upgrade, so the browser sees an HTTP 403 on the
   * handshake and reports a plain abnormal closure — indistinguishable from
   * an unreachable server. There is no close code to read in that case.
   */
  opened: boolean;
  /** True when the transport has stopped retrying by itself. */
  terminal: boolean;
}

export interface ChainlitSocketOptions {
  /** Absolute `ws://` or `wss://` URL of the websocket route. */
  url: string;
  /**
   * Builds the handshake frame. Called once per connection attempt, so the
   * thread id, chat profile and page-load flag are read at the moment the
   * attempt is made rather than frozen when the socket was constructed.
   */
  hello: () => Hello;
  onStatus?: (status: ConnectionStatus) => void;
  /** Fires on every close, retried or not. */
  onClose?: (info: CloseInfo) => void;
}

/** Build the websocket URL from the API's HTTP endpoint. */
export const websocketUrl = (httpEndpoint: string): string => {
  const { protocol, host, pathname } = new URL(
    httpEndpoint,
    typeof window !== 'undefined' ? window.location.href : undefined
  );
  const scheme = protocol === 'https:' ? 'wss:' : 'ws:';
  const root = pathname.replace(/\/+$/, '');
  return `${scheme}//${host}${root}${WEBSOCKET_PATH}`;
};

/**
 * A reconnecting, buffering websocket that speaks the Chainlit protocol.
 *
 * Authentication is by cookie only: the browser sends the session cookies
 * with the upgrade request, and nothing is put in the URL or the handshake
 * frame. That is why a refusal arrives as an HTTP 403 on the upgrade rather
 * than as a close frame — see {@link CloseInfo.opened}.
 */
export class ChainlitSocket {
  /**
   * Messages waiting for a usable connection, oldest first.
   *
   * Public and mutable-looking on purpose: the e2e suite asserts on its
   * length to prove a click happened while the transport was down. Nothing
   * in the application writes to it.
   */
  readonly sendBuffer: ClientMsg[] = [];

  private readonly options: ChainlitSocketOptions;
  private readonly listeners = new Set<(message: ServerMsg) => void>();
  private ws?: WebSocket;
  /** True between `session.ready` and the close that follows it. */
  private ready = false;
  private closedByUs = false;
  private reconnectEnabled = true;
  private attempt = 0;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private watchdogTimer?: ReturnType<typeof setTimeout>;
  private watchdogMs = DEFAULT_HEARTBEAT_MS * HEARTBEAT_GRACE;
  private status: ConnectionStatus = 'closed';

  constructor(options: ChainlitSocketOptions) {
    this.options = options;
  }

  /**
   * Receive every decoded server message, in arrival order. Returns the
   * unsubscribe function.
   *
   * Several parts of the UI listen at once — the session hook keeps the
   * exhaustive handler table, while the components that need the router
   * pick out the two or three tags they act on.
   */
  subscribe(listener: (message: ServerMsg) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** True once the handshake completed; false the moment the socket drops. */
  get connected(): boolean {
    return this.ready && this.ws?.readyState === WebSocket.OPEN;
  }

  /**
   * True while this transport is still trying: open, opening, or between
   * retries. False once it was closed deliberately or gave up.
   */
  get alive(): boolean {
    return (
      !this.closedByUs &&
      (this.status !== 'closed' || this.retryTimer !== undefined)
    );
  }

  /** Open the socket, or do nothing if one is already open or opening. */
  connect(): void {
    this.closedByUs = false;
    this.clearRetry();
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this.open();
  }

  /**
   * Close deliberately and stay closed. The buffer is kept: a `clear()`
   * followed by a fresh session builds a new transport anyway, and dropping
   * queued work here would lose it silently.
   */
  close(): void {
    this.closedByUs = true;
    this.clearRetry();
    this.clearWatchdog();
    this.teardown();
    this.setStatus('closed');
  }

  /** Alias of {@link close}, for call sites that read better that way. */
  disconnect(): void {
    this.close();
  }

  /**
   * Queue a message, and write everything queued if the session is usable.
   *
   * Ordering is strictly the order of the `send` calls. A message handed
   * over while a reconnect is in flight simply joins the tail of the buffer
   * and goes out with the rest once `session.ready` arrives — including one
   * queued from inside the drain itself, because `send` always appends
   * before it drains.
   */
  send(message: ClientMsg): void {
    if (LIVE_ONLY.has(message.t)) {
      if (this.ws?.readyState === WebSocket.OPEN) this.write(message);
      return;
    }
    this.sendBuffer.push(message);
    this.flush();
  }

  /**
   * Turn automatic reconnection on or off, and report the current setting.
   * Exists for the e2e suite, which has to hold the transport down while it
   * makes a click; nothing in the application calls it.
   */
  setReconnection(enabled?: boolean): boolean {
    if (enabled !== undefined) this.reconnectEnabled = enabled;
    return this.reconnectEnabled;
  }

  /**
   * Drop the underlying socket the way a dead network would, without
   * marking the close deliberate — so the reconnect policy applies. Also
   * used by the silence watchdog.
   */
  drop(): void {
    if (!this.ws) return;
    this.teardown(true);
  }

  // ----------------------------------------------------------------------
  // Connection lifecycle
  // ----------------------------------------------------------------------

  private open(): void {
    this.teardown();
    this.ready = false;
    this.setStatus('connecting');

    let socket: WebSocket;
    try {
      socket = new WebSocket(this.options.url);
    } catch (error) {
      // A malformed URL, or websockets unavailable. Treat it as a failed
      // attempt so the backoff still applies instead of dying silently.
      console.error('Failed to open the Chainlit websocket:', error);
      this.handleClose(1006, String(error), false);
      return;
    }

    this.ws = socket;
    let opened = false;

    socket.onopen = () => {
      opened = true;
      // Until `session.ready` names an interval, the default one bounds the
      // handshake itself.
      this.watchdogMs = Math.max(
        DEFAULT_HEARTBEAT_MS * HEARTBEAT_GRACE,
        MIN_WATCHDOG_MS
      );
      this.armWatchdog();
      // The handshake frame goes out first and bypasses the buffer: it is
      // rebuilt per attempt, and the buffer must not be drained until the
      // server has answered `session.ready`. socket.io flushed its buffer
      // *before* announcing itself, which is how buffered events used to
      // reach a half-initialised session.
      const hello = this.options.hello();
      console.debug('[chainlit] hello ' + JSON.stringify(hello));
      this.write(hello);
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        console.warn('Ignoring a non-text websocket frame.');
        return;
      }
      let message: ServerMsg;
      try {
        message = JSON.parse(event.data) as ServerMsg;
      } catch (error) {
        console.error('Malformed websocket frame:', error);
        return;
      }
      this.receive(message);
    };

    socket.onerror = () => {
      // `onclose` always follows, and carries the code. Nothing to do here
      // beyond keeping the browser from logging an unhandled error event.
    };

    socket.onclose = (event) => {
      if (socket !== this.ws) return; // superseded by a newer attempt
      this.handleClose(event.code, event.reason, opened);
    };
  }

  private receive(message: ServerMsg): void {
    // Any inbound frame proves the connection is alive, so any inbound frame
    // re-arms the watchdog — not just the heartbeat.
    this.armWatchdog();

    switch (message.t) {
      case 'hb':
        this.send({ t: 'hb.ack', seq: message.seq ?? 0 });
        break;
      case 'session.ready':
        this.attempt = 0;
        this.ready = true;
        this.watchdogMs = Math.max(
          (message.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_MS) *
            HEARTBEAT_GRACE,
          MIN_WATCHDOG_MS
        );
        this.armWatchdog();
        this.setStatus('ready');
        break;
      default:
        break;
    }

    for (const listener of [...this.listeners]) {
      try {
        listener(message);
      } catch (error) {
        console.error('A websocket listener threw:', error);
      }
    }

    // After the listeners, so a handler that reacts to `session.ready` by
    // sending something keeps its place at the tail of the queue rather
    // than jumping ahead of messages buffered before the drop.
    if (message.t === 'session.ready') this.flush();
  }

  private handleClose(code: number, reason: string, opened: boolean): void {
    this.ready = false;
    this.ws = undefined;
    this.clearWatchdog();

    const terminal =
      this.closedByUs ||
      !this.reconnectEnabled ||
      TERMINAL_CLOSE_CODES.has(code);

    this.setStatus('closed');
    this.options.onClose?.({ code, reason, opened, terminal });

    if (terminal) return;
    this.scheduleRetry();
  }

  private scheduleRetry(): void {
    this.clearRetry();
    // Exponential with full jitter in the upper half of the window: fast
    // enough that a blip is invisible, spread out enough that a restarted
    // server is not stampeded by every open tab at once.
    const window = Math.min(
      BACKOFF_BASE_MS * 2 ** this.attempt,
      BACKOFF_MAX_MS
    );
    this.attempt += 1;
    const delay = window / 2 + Math.random() * (window / 2);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = undefined;
      if (this.closedByUs || !this.reconnectEnabled) return;
      this.open();
    }, delay);
  }

  // ----------------------------------------------------------------------
  // Outbound
  // ----------------------------------------------------------------------

  private flush(): void {
    while (this.sendBuffer.length) {
      if (!this.ready || this.ws?.readyState !== WebSocket.OPEN) return;
      // Peek, write, then drop: a write that throws leaves the message at
      // the head of the queue instead of losing it.
      this.write(this.sendBuffer[0]);
      this.sendBuffer.shift();
    }
  }

  private write(message: ClientMsg): void {
    this.ws?.send(JSON.stringify(message));
  }

  // ----------------------------------------------------------------------
  // Liveness
  // ----------------------------------------------------------------------

  /**
   * A half-open socket — the peer vanished without a FIN — never fires
   * `onclose`, so nothing would ever trigger the reconnect. The server's
   * heartbeat gives the client a clock to hold it to.
   *
   * Armed from `onopen`, not from `session.ready`: a server that accepts the
   * upgrade and then never answers the handshake produces no close event
   * either, and without a clock running from the moment the socket opened
   * the transport would sit in `connecting` forever.
   */
  private armWatchdog(): void {
    this.clearWatchdog();
    this.watchdogTimer = setTimeout(() => {
      this.watchdogTimer = undefined;
      console.warn(
        this.ready
          ? 'No heartbeat from the server; reconnecting.'
          : 'The server never completed the handshake; reconnecting.'
      );
      this.drop();
    }, this.watchdogMs);
  }

  private clearWatchdog(): void {
    if (this.watchdogTimer !== undefined) clearTimeout(this.watchdogTimer);
    this.watchdogTimer = undefined;
  }

  private clearRetry(): void {
    if (this.retryTimer !== undefined) clearTimeout(this.retryTimer);
    this.retryTimer = undefined;
  }

  /**
   * Detach and close the current socket.
   *
   * With `notify` the close is reported as an abnormal one, so the reconnect
   * policy applies; without it the socket is abandoned silently, which is
   * what a deliberate close or a replacement attempt wants. Either way the
   * handlers come off first, so the browser's own `close` event — which
   * arrives a tick later — cannot report the same drop twice.
   */
  private teardown(notify = false): void {
    const socket = this.ws;
    if (!socket) return;
    this.ws = undefined;
    this.ready = false;
    socket.onclose = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onopen = null;
    try {
      socket.close();
    } catch {
      // Already closing; nothing to do.
    }
    if (notify) this.handleClose(1006, 'dropped', true);
  }

  private setStatus(status: ConnectionStatus): void {
    if (status === this.status) return;
    this.status = status;
    this.options.onStatus?.(status);
  }
}
