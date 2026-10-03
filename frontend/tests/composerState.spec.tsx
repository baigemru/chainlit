import { act, render } from '@testing-library/react';
import { useEffect } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it } from 'vitest';

// The package's sources, not its bundle -- see `elementSidebarFrame.spec.tsx`
// for why a `.ts` import is the only one that resolves a single React.
import { ChatTransportContext } from '../../libs/react-client/src/context';
import type { ServerMsg } from '../../libs/react-client/src/protocol';
import type { SessionSink } from '../../libs/react-client/src/transport';
import { useChatData } from '../../libs/react-client/src/useChatData';
import { useChatInteract } from '../../libs/react-client/src/useChatInteract';
import { useChatSession } from '../../libs/react-client/src/useChatSession';

/**
 * The `composer.state` handler, through the real session sink and the real
 * atom, read back the way the composer reads it -- `useChatData().composer`.
 *
 * The frame is the whole state, so what is under test is mostly what the
 * handler must *not* do: keep a field the latest frame left out, or let the
 * words of a conversation outlive the session that said them.
 */

let sink: SessionSink | undefined;

// One object, not one per call: `useChatData` reads it through
// `useSyncExternalStore`, which re-renders whenever the snapshot is a new
// object -- a fresh literal each time is a render loop.
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
  send: () => undefined,
  subscribe: () => () => undefined,
  getSnapshot: () => snapshot,
  onMessage: () => () => undefined
};

let composer: ReturnType<typeof useChatData>['composer'];
let clear: ReturnType<typeof useChatInteract>['clear'];

const Probe = () => {
  const { attach } = useChatSession();
  composer = useChatData().composer;
  clear = useChatInteract().clear;
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
  sink = undefined;
});

describe('the composer.state handler', () => {
  it('starts at the configured default', () => {
    mount();

    expect(composer).toEqual({});
  });

  it('writes what the server said', () => {
    mount();

    deliver({
      t: 'composer.state',
      placeholder: 'Add a correction',
      hint: '41 of 60 left'
    });

    expect(composer).toEqual({
      placeholder: 'Add a correction',
      hint: '41 of 60 left'
    });
  });

  it('replaces the state whole, never merges into it', () => {
    mount();

    deliver(
      { t: 'composer.state', placeholder: 'Add a correction', hint: 'a' },
      { t: 'composer.state', hint: 'b' }
    );

    // The placeholder the second frame left out is the default again, not
    // the one before: the frame is a state, and a missing field is "as
    // configured".
    expect(composer).toEqual({ hint: 'b' });
  });

  it('reads a bare frame as the default, and null as absent', () => {
    mount();

    deliver(
      { t: 'composer.state', placeholder: 'x', hint: 'y' },
      { t: 'composer.state', placeholder: null, hint: null }
    );
    expect(composer).toEqual({});

    deliver({ t: 'composer.state', hint: 'y' }, { t: 'composer.state' });
    expect(composer).toEqual({});
  });

  it('keeps an empty string, which is an answer', () => {
    mount();

    deliver({ t: 'composer.state', hint: '' });

    expect(composer).toEqual({ hint: '' });
  });

  it('is forgotten by a new chat', () => {
    // The successor's replay states its own; until it lands, the old
    // conversation's words must not sit over the new one.
    mount();
    deliver({ t: 'composer.state', placeholder: 'Add a correction' });

    act(() => clear());

    expect(composer).toEqual({});
  });
});
