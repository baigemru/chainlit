import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ServerMsg } from '@chainlit/react-client';

import ThreadAddressSync from '@/components/ThreadAddressSync';

/**
 * Both halves of "the URL asks and `session.ready` answers", in the one
 * component that owns them.
 *
 * `threadAddress.spec.ts` covers which addresses may move; this covers when
 * a move is allowed at all — an app that cannot resume must never get a live
 * address (a reload there lands on `Thread.tsx`'s read-only view of a
 * conversation the user was typing in), a `/share/` page must not be yanked
 * to the live chat, and the move is a replace so Back still leads where the
 * user came from — and when a route counts as a *request*: only when it
 * names a thread that is neither the one the session is in nor the one the
 * descriptor already asked for.
 */

const mockNavigate = vi.fn();
let pathname = '/';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname })
}));

const mockToastError = vi.fn();
// Wrapped rather than passed: `vi.mock` factories are hoisted, and naming
// the spy directly in the returned object reads it before it exists.
vi.mock('sonner', () => ({
  toast: { error: (...args: unknown[]) => mockToastError(...args) }
}));

const mockUseConfig = vi.fn();
const mockClear = vi.fn();

// The listener's inputs. `onMessage` is the transport's additive fan-out, so
// the fake hands back the listener it was given and an unsubscribe, exactly
// as `ChatTransport.onMessage` does.
let listeners: ((message: ServerMsg) => void)[] = [];
const transport = {
  onMessage: (listener: (message: ServerMsg) => void) => {
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((l) => l !== listener);
    };
  }
};

// The descriptor's thread (what this tab asked for), the session's thread
// (what the server answered) and the transport's error flag.
let idToResume: string | undefined;
let currentThreadId: string | undefined;
let transportError = false;

vi.mock('@chainlit/react-client', () => ({
  useChatTransport: () => transport,
  useConfig: () => mockUseConfig(),
  useChatInteract: () => ({ clear: mockClear }),
  useChatSession: () => ({ idToResume }),
  useChatData: () => ({ error: transportError }),
  // Only ever handed back to `useRecoilValue`, which is answered by key
  // below, so a real atom would buy nothing but a RecoilRoot to hold it.
  currentThreadIdState: { key: 'CurrentThreadId' }
}));

// Answered by key rather than by a store: the component reads exactly two
// atoms, and standing a RecoilRoot up for them would drag in every setter
// `clear` and the kept-transcript reset close over.
vi.mock('recoil', async () => {
  const actual = await vi.importActual<typeof import('recoil')>('recoil');
  return {
    ...actual,
    useRecoilValue: (node: { key: string }) =>
      node.key === 'CurrentThreadId' ? currentThreadId : undefined
  };
});

const mockResetKeptTranscript = vi.fn();
vi.mock('@/hooks/useParentThread', () => ({
  useResetKeptTranscript: () => mockResetKeptTranscript
}));

const ready = (threadId?: string) =>
  listeners.forEach((l) => l({ t: 'session.ready', threadId } as ServerMsg));

const mount = (
  at: string,
  threadResumable: boolean,
  { dataPersistence = true }: { dataPersistence?: boolean } = {}
) => {
  pathname = at;
  mockUseConfig.mockReturnValue({
    config: { threadResumable, dataPersistence }
  });
  return render(<ThreadAddressSync />);
};

beforeEach(() => {
  vi.clearAllMocks();
  listeners = [];
  pathname = '/';
  idToResume = undefined;
  currentThreadId = undefined;
  transportError = false;
});

describe('ThreadAddressSync — session.ready answers', () => {
  it('names the chat begun at `/` the moment the server answers', () => {
    mount('/', true);

    ready('t1');

    expect(mockNavigate).toHaveBeenCalledTimes(1);
    // Replace, not push: the user is already looking at this conversation,
    // it is only being given a name.
    expect(mockNavigate).toHaveBeenCalledWith('/thread/t1', { replace: true });
  });

  it('does not navigate again for the same thread', () => {
    const { rerender } = mount('/', true);

    ready('t1');
    // What the router would have done with the replace above. The component
    // has no memory of its own — `threadAddressFor` answers null once the
    // address is already the target — and it reads the pathname through a
    // ref assigned during render, so the rerender is what publishes it.
    pathname = '/thread/t1';
    idToResume = 't1';
    currentThreadId = 't1';
    // A fresh element: React bails out of re-rendering the identical one, and
    // the pathname this component reads is a ref assigned during render.
    rerender(<ThreadAddressSync />);
    ready('t1');

    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });

  it('leaves the address alone for an app that cannot resume', () => {
    // Every session gets a thread from its first frame, but an app with no
    // resume hooks starts over on a reload — an address pointing at a
    // thread would come back as a frozen read-only copy, or a 404 for a
    // thread that has no row yet.
    mount('/', false);

    ready('t1');

    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('never moves a share page', () => {
    // A view of somebody else's conversation. A reconnect is not a reason
    // to throw away the page the user opened.
    mount('/share/x', true);

    ready('t1');

    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('follows the server onto a different thread', () => {
    // The refused resume, which is no longer an error frame: the server
    // moved the session to a thread of its own and named it here.
    idToResume = 't0';
    mount('/thread/t0', true);

    ready('t1');

    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/thread/t1', { replace: true });
  });

  it('ignores a ready that names no thread', () => {
    mount('/', true);

    ready(undefined);

    expect(mockNavigate).not.toHaveBeenCalled();
  });
});

describe('ThreadAddressSync — the URL asks', () => {
  it('requests the thread the route names when nothing else has', () => {
    // A click in the history list, or a link pasted into the bar: the route
    // names a conversation this tab is neither in nor asking for.
    mount('/thread/X', true);

    expect(mockClear).toHaveBeenCalledTimes(1);
    expect(mockClear).toHaveBeenCalledWith({ threadId: 'X' });
  });

  it('asks for nothing when the route names the thread the session is in', () => {
    // The ordinary steady state: `session.ready` named the thread and the
    // address followed it. Reading that back as a request would clear the
    // session on every render.
    currentThreadId = 'X';
    mount('/thread/X', true);

    expect(mockClear).not.toHaveBeenCalled();
  });

  it('asks for nothing when the descriptor is already asking for it', () => {
    // The resume is out and has not been answered yet -- the state a fresh
    // load at /thread/X is in, seeded before RecoilRoot mounts. Without this
    // guard the config refetch that follows a resume (the key carries the
    // profile the thread turned out to have) would re-run the effect and
    // clear the session it is waiting on.
    idToResume = 'X';
    mount('/thread/X', true);

    expect(mockClear).not.toHaveBeenCalled();
  });

  it('asks for nothing on a route that names no thread', () => {
    mount('/share/X', true);

    expect(mockClear).not.toHaveBeenCalled();
  });

  it('gives the thread up and goes home when the resume fails', () => {
    // Gated on the descriptor, because on the commit that issues the resume
    // it has been requested but not rendered, and an error left over from
    // the session `clear()` just dropped would be read as this one's answer.
    idToResume = 'X';
    transportError = true;
    mount('/thread/X', true);

    expect(mockToastError).toHaveBeenCalledWith("Couldn't resume chat");
    // Released, not kept: the descriptor is the guard above, so holding on
    // to the thread would make picking it out of the history again a no-op.
    expect(mockClear).toHaveBeenCalledWith();
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('leaves a live chat alone when the transport dies later', () => {
    // The resume landed; this error is a dead connection, not a refusal.
    // Bouncing the user out of the conversation they are reading for it is
    // what the `currentThreadId` half of the guard prevents.
    idToResume = 'X';
    currentThreadId = 'X';
    transportError = true;
    mount('/thread/X', true);

    expect(mockToastError).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
