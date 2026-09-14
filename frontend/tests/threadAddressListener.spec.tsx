import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ServerMsg } from '@chainlit/react-client';

import ThreadAddressListener from '@/components/ThreadAddressListener';

/**
 * The answering half of "the URL asks and `session.ready` answers".
 *
 * `threadAddress.spec.ts` covers which addresses may move; this covers when
 * the listener is allowed to move one at all — an app that cannot resume
 * must never get a live address (a reload there lands on `Thread.tsx`'s
 * read-only view of a conversation the user was typing in), a `/share/`
 * page must not be yanked to the live chat, and the move is a replace so
 * Back still leads where the user came from.
 */

const mockNavigate = vi.fn();
let pathname = '/';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname })
}));

const mockUseConfig = vi.fn();

// The listener's only two inputs. `onMessage` is the transport's additive
// fan-out, so the fake hands back the listener it was given and an
// unsubscribe, exactly as `ChatTransport.onMessage` does.
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
  useConfig: () => mockUseConfig()
}));

const ready = (threadId?: string) =>
  listeners.forEach((l) => l({ t: 'session.ready', threadId } as ServerMsg));

const mount = (at: string, threadResumable: boolean) => {
  pathname = at;
  mockUseConfig.mockReturnValue({ config: { threadResumable } });
  return render(<ThreadAddressListener />);
};

beforeEach(() => {
  vi.clearAllMocks();
  listeners = [];
  pathname = '/';
});

describe('ThreadAddressListener', () => {
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
    // What the router would have done with the replace above. The listener
    // has no memory of its own — `threadAddressFor` answers null once the
    // address is already the target — and it reads the pathname through a
    // ref assigned during render, so the rerender is what publishes it.
    pathname = '/thread/t1';
    rerender(<ThreadAddressListener />);
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
