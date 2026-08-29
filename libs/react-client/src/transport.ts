/**
 * The single owner of the connection.
 *
 * Before this, seven places created or closed a socket and every one of them
 * carried a guard against the other six: a ref mirroring the Recoil atom the
 * socket lived in, a second ref remembering which session id the live socket
 * was built for, a 200ms debounce with a trailing `cancel`, and a
 * `ChainlitSocket.alive` getter that existed only so a caller could ask "did
 * somebody else already do this?". None of them are here, because the
 * question they answered is now answered in one place.
 *
 * The model is: navigation states a **descriptor** — the session id, and the
 * thread it resumes — and the transport is told to `attach` to it. Attaching
 * to the descriptor already attached is nothing. Attaching to a different one
 * closes what is open and opens the new one, exactly once, with no window in
 * which a stale attach can act.
 *
 * What is deliberately *not* part of that identity is the hello payload: the
 * chat profile, the live thread id and the user env travel in every handshake
 * and change while a session is running. The server announces a profile of
 * its own on `session.ready` and on `thread.resume`; treating that as a new
 * descriptor would tear the socket down to tell the server the thing the
 * server just told us, and replay the whole resume for nothing.
 */
import type { ChainlitAPI } from './api';
import type { ClientMsg, Hello, ServerMsg } from './protocol';
import { CloseCode } from './protocol';
import type { CloseInfo } from './socket';
import { ChainlitSocket, websocketUrl } from './socket';

/**
 * True once any connection succeeded in this page's lifetime. Reported to the
 * server in `hello` so it can tell a reconnect of a loaded page (UI state
 * intact) from a fresh page load that needs a full restore of a pending ask's
 * transcript, actions and element.
 */
let pageHasEstablishedConnection = false;

/**
 * For embedders that unmount and remount the whole widget (copilot): the
 * remounted UI starts empty, so the next connect must be treated as a fresh
 * load again or the server would skip the full restore.
 */
export const resetPageConnectionFlag = () => {
  pageHasEstablishedConnection = false;
};

/**
 * What the application decided this connection is for.
 *
 * The two fields are the connection's identity: changing either of them means
 * a different conversation, and the transport rebuilds. `chatProfile` rides
 * along because the descriptor is also what the app offers in `hello`, but it
 * is payload, not identity — see the module docstring.
 */
export interface SessionDescriptor {
  sessionId: string;
  /** The thread this session was opened to resume, if any. */
  threadId?: string;
  /** The profile the client offers. The server's answer wins. */
  chatProfile?: string;
}

/** Everything else the handshake frame carries, read at attach time. */
export interface HelloPayload {
  /**
   * The thread the session is actually in, which the server names after the
   * first interaction and on a resume. Wins over the descriptor's resume
   * target: a session that has moved on must not offer to resume the thread
   * it started from.
   */
  threadId?: string;
  chatProfile?: string;
  userEnv?: Record<string, string>;
}

/**
 * Where the connection stands.
 *
 * `reconnecting` is a drop the transport will heal by itself; `closed` is one
 * it gave up on, and only a fresh `attach` revives it. `superseded` is close
 * 4409 and is sticky on purpose: the session belongs to another connection
 * now, and re-attaching the same descriptor would see-saw it between tabs.
 */
export type TransportPhase =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'reconnecting'
  | 'closed'
  | 'superseded';

export interface TransportState {
  phase: TransportPhase;
  /** The handshake completed and the socket is open. */
  connected: boolean;
  /** Something the user should be told about, as opposed to a healing blip. */
  error: boolean;
  /** Close 4409: this session is being spoken for somewhere else. */
  superseded: boolean;
}

/**
 * The one place a `hello` is answered and a close is acted on.
 *
 * A single slot, not a fan-out: `step.stream.token` appends, so a handler
 * table registered twice would double every streamed token. Ten components
 * call `useChatSession`; only the one that attaches installs this.
 */
export interface SessionSink {
  onFrame(message: ServerMsg): void;
  onClose(info: CloseInfo): void;
}

const IDLE: TransportState = {
  phase: 'idle',
  connected: false,
  error: false,
  superseded: false
};

const sameSession = (a: SessionDescriptor, b: SessionDescriptor): boolean =>
  a.sessionId === b.sessionId &&
  (a.threadId || undefined) === (b.threadId || undefined);

export class ChatTransport {
  /**
   * Messages waiting for a usable connection, oldest first.
   *
   * Owned here rather than by the socket, so it survives the socket being
   * replaced: a click made during a blip is still delivered after a rebuild,
   * not only after an automatic reconnect. The e2e suite asserts on its
   * length; nothing in the application writes to it.
   */
  readonly sendBuffer: ClientMsg[] = [];

  /**
   * The API this transport speaks to. Mutable because the copilot builds a
   * fresh `ChainlitAPI` in its render body — same server, new object.
   */
  client: ChainlitAPI;

  private state: TransportState = IDLE;
  private readonly watchers = new Set<() => void>();
  private readonly listeners = new Set<(message: ServerMsg) => void>();
  private sink?: SessionSink;

  private socket?: ChainlitSocket;
  private descriptor?: SessionDescriptor;
  private payload: HelloPayload = {};
  /**
   * Bumped by every `attach` and `detach`. An attach has to await the sticky
   * cookie before it may open, and this is what tells its continuation that a
   * newer intent overtook it in the meantime — the stale one then does
   * nothing at all, rather than closing the socket the newer one just opened.
   */
  private generation = 0;

  constructor(client: ChainlitAPI) {
    this.client = client;
    this.installCypressHandle();
  }

  // ------------------------------------------------------------- lifecycle

  /**
   * Speak for this descriptor. Idempotent: attaching to the descriptor
   * already attached only refreshes the hello payload.
   */
  attach = (
    descriptor: SessionDescriptor,
    payload: HelloPayload = {}
  ): void => {
    this.payload = { ...payload };

    if (this.descriptor && sameSession(this.descriptor, descriptor)) {
      this.descriptor = descriptor;
      // Open, opening, or dropping and healing on its own: nothing to do.
      // `closed` is the one phase that falls through — the transport gave up
      // (bad credentials, say) and this attach is the retry, which is how a
      // connection comes back after the user logs in again.
      if (this.state.phase !== 'closed') return;
    }

    const generation = ++this.generation;
    this.descriptor = descriptor;
    this.dropSocket();
    this.publish({ phase: 'connecting', connected: false, error: false });
    void this.openWhenPinned(generation);
  };

  /** Stop speaking for anything. The buffer is kept. */
  detach = (): void => {
    this.generation += 1;
    this.descriptor = undefined;
    this.dropSocket();
    this.publish(IDLE);
  };

  /** Queue a message, and write it now if the session is usable. */
  send = (message: ClientMsg): void => {
    this.socket?.send(message);
  };

  /** The descriptor currently attached, if any. */
  get attached(): SessionDescriptor | undefined {
    return this.descriptor;
  }

  // ---------------------------------------------------------------- stores

  /** `useSyncExternalStore` subscription for {@link getSnapshot}. */
  subscribe = (watcher: () => void): (() => void) => {
    this.watchers.add(watcher);
    return () => {
      this.watchers.delete(watcher);
    };
  };

  getSnapshot = (): TransportState => this.state;

  /**
   * Receive every decoded server message, in arrival order. Additive, and
   * kept across socket rebuilds — a subscriber no longer re-registers every
   * time the connection is replaced.
   */
  onMessage = (listener: (message: ServerMsg) => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Install the single session sink. See {@link SessionSink}. */
  setSink = (sink: SessionSink | undefined): void => {
    this.sink = sink;
  };

  // ------------------------------------------------------------- internals

  private async openWhenPinned(generation: number): Promise<void> {
    try {
      await this.client.stickyCookie(this.descriptor!.sessionId);
    } catch (error) {
      console.error(`Failed to set sticky session cookie: ${error}`);
    }
    // A `clear()` (or another navigation) landed while the cookie was in
    // flight. The attach it belongs to has already closed whatever was open
    // and is opening its own socket; this one must not touch either.
    if (generation !== this.generation) return;
    this.open(generation);
  }

  private open(generation: number): void {
    const socket = new ChainlitSocket({
      url: websocketUrl(this.client.httpEndpoint),
      buffer: this.sendBuffer,
      hello: () => this.hello(),
      onStatus: (status) => {
        if (generation !== this.generation) return;
        if (status === 'connecting') {
          this.publish({ phase: 'connecting', connected: false });
        } else if (status === 'ready') {
          this.publish({ phase: 'ready', connected: true, error: false });
        }
        // 'closed' is left to onClose, which knows whether it is final.
      },
      onClose: (info) => {
        if (generation !== this.generation) return;
        this.closed(info);
      }
    });
    this.socket = socket;
    socket.subscribe(this.dispatch);
    socket.connect();
  }

  private closed(info: CloseInfo): void {
    if (info.code === CloseCode.SUPERSEDED) {
      // Not an error the user can act on, and not a failure to resume: the
      // chat is simply being had somewhere else.
      this.publish({
        phase: 'superseded',
        connected: false,
        error: false,
        superseded: true
      });
    } else if (info.terminal) {
      this.publish({ phase: 'closed', connected: false, error: true });
    } else if (!info.opened) {
      // An upgrade refused before the server accepted it (no close frame to
      // read) or an unreachable server. The retry that follows will report
      // itself, but the user is owed an answer now.
      this.publish({ phase: 'reconnecting', connected: false, error: true });
    } else {
      // A drop the transport is about to heal. Not the UI's business, and
      // the error flag keeps whatever it already said.
      this.publish({ phase: 'reconnecting', connected: false });
    }
    this.sink?.onClose(info);
  }

  private dispatch = (message: ServerMsg): void => {
    if (message.t === 'session.ready') pageHasEstablishedConnection = true;
    // The sink first: it holds the exhaustive handler table, and the
    // router-bound listeners react to state it has already written.
    try {
      this.sink?.onFrame(message);
    } catch (error) {
      console.error('The session handler threw:', error);
    }
    for (const listener of [...this.listeners]) {
      try {
        listener(message);
      } catch (error) {
        console.error('A websocket listener threw:', error);
      }
    }
  };

  private hello(): Hello {
    const descriptor = this.descriptor;
    return {
      t: 'hello',
      sessionId: descriptor?.sessionId ?? '',
      clientType: this.client.type,
      threadId: this.payload.threadId || descriptor?.threadId || undefined,
      chatProfile:
        this.payload.chatProfile || descriptor?.chatProfile || undefined,
      userEnv: this.payload.userEnv,
      // True only on the very first connect after a full page load: the
      // server restores the old session then only to rescue a live pending
      // ask; otherwise a reload means a fresh chat.
      pageLoad: !pageHasEstablishedConnection
    };
  }

  private dropSocket(): void {
    const socket = this.socket;
    if (!socket) return;
    this.socket = undefined;
    socket.close();
  }

  private publish(next: Partial<TransportState>): void {
    const merged: TransportState = {
      ...this.state,
      superseded: false,
      ...next
    };
    if (
      merged.phase === this.state.phase &&
      merged.connected === this.state.connected &&
      merged.error === this.state.error &&
      merged.superseded === this.state.superseded
    ) {
      return;
    }
    this.state = merged;
    for (const watcher of [...this.watchers]) watcher();
  }

  /**
   * The handle the e2e suite drives the transport through, shaped like the
   * socket.io object the specs were written against. It hangs off the
   * transport rather than off one socket, so it keeps working after a
   * rebuild. Only under Cypress, and never for the copilot widget: a handle
   * on the user's socket must not leak to page scripts in production.
   */
  private installCypressHandle(): void {
    if (typeof window === 'undefined') return;
    if (!(window as any).Cypress || this.client.type === 'copilot') return;
    const connected = () => this.state.connected;
    const buffer = this.sendBuffer;
    (window as any).__chainlitSocket = {
      get connected() {
        return connected();
      },
      get sendBuffer() {
        return buffer;
      },
      connect: () => this.socket?.connect(),
      close: () => this.socket?.close(),
      io: {
        reconnection: (enabled?: boolean) =>
          this.socket?.setReconnection(enabled) ?? true,
        engine: { close: () => this.socket?.drop() }
      }
    };
  }
}

/**
 * One transport per server, keyed by what identifies the server rather than
 * by the client object: the copilot rebuilds its `ChainlitAPI` on every
 * render of the widget wrapper, and a registry keyed on identity would hand
 * out a new transport each time.
 */
const transports = new Map<string, ChatTransport>();

export const chatTransportFor = (client: ChainlitAPI): ChatTransport => {
  const key = `${client.type} ${client.httpEndpoint}`;
  const existing = transports.get(key);
  if (existing) {
    existing.client = client;
    return existing;
  }
  const created = new ChatTransport(client);
  transports.set(key, created);
  return created;
};
