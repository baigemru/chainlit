import { act, render } from '@testing-library/react';
import { useEffect } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The package's *sources*, not its bundle. Reached through the workspace's
// `node_modules` symlink the built `.mjs` is externalised, and the `react`
// inside it resolves to the copy under `libs/react-client` -- a second
// React, whose hook dispatcher is null under this react-dom. A `.ts` import
// has to go through vite, which resolves react once.
import { ChatTransportContext } from '../../libs/react-client/src/context';
import type {
  ClientMsg,
  ServerMsg
} from '../../libs/react-client/src/protocol';
import type { SessionSink } from '../../libs/react-client/src/transport';
import type { IElementSidebarState } from '../../libs/react-client/src/types';
import { useChatSession } from '../../libs/react-client/src/useChatSession';
import { useElementSidebar } from '../../libs/react-client/src/useElementSidebar';

/**
 * The `sidebar.state` handler, against a transport that is a stub but a
 * truthful one: the frames go through the real `useChatSession` sink and the
 * real atom, because what is under test is the resolution of `elementIds`
 * against elements that arrived a frame earlier — and that only means
 * anything if both writes go through the same store.
 */

const sent: ClientMsg[] = [];
let sink: SessionSink | undefined;

/** The viewport the rule and the layout both read. See `breakpoint.ts`. */
const widen = (width: number) => {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: width
  });
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
  getSnapshot: () => ({
    phase: 'ready' as const,
    connected: true,
    error: false,
    superseded: false
  }),
  onMessage: () => () => undefined
};

let panel: IElementSidebarState;
let dispatch: ReturnType<typeof useElementSidebar>['dispatch'];

const Probe = () => {
  const { attach } = useChatSession();
  const sidebar = useElementSidebar();
  panel = sidebar.state;
  dispatch = sidebar.dispatch;
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

const ready = (): ServerMsg => ({
  t: 'session.ready',
  sessionId: 's1',
  threadId: 't1'
});

const upsert = (id: string, name: string): ServerMsg => ({
  t: 'element.upsert',
  element: { type: 'text', id, name, url: `/x/${id}` } as never
});

beforeEach(() => {
  sent.length = 0;
  sink = undefined;
  widen(1280);
});

describe('the sidebar.state handler', () => {
  it('resolves the slots elementIds against the elements that arrived first', () => {
    mount();

    deliver(ready(), upsert('el-1', 'cards'), upsert('el-2', 'report'), {
      t: 'sidebar.state',
      slots: [
        { id: 'cards', title: 'Shortlist', elementIds: ['el-1'] },
        {
          id: 'report',
          title: 'Report',
          elementIds: ['el-2'],
          closable: false,
          canvas: true
        }
      ],
      active: 'report',
      visible: true
    });

    expect(panel.slots.map((slot) => slot.id)).toEqual(['cards', 'report']);
    expect(panel.slots[0].elements.map((e) => e.name)).toEqual(['cards']);
    expect(panel.slots[1].closable).toBe(false);
    expect(panel.slots[1].canvas).toBe(true);
    expect(panel.active).toBe('report');
    expect(panel.visible).toBe(true);
  });

  it('skips an id that never arrived and says so', () => {
    // A server bug, not a race: the elements go out ahead of the frame on
    // one FIFO queue. Drawing a hole for it would hide that.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    mount();

    deliver(ready(), upsert('el-1', 'cards'), {
      t: 'sidebar.state',
      slots: [{ id: 'cards', elementIds: ['el-1', 'ghost'] }],
      active: 'cards',
      visible: true,
      rev: 4
    });

    expect(panel.slots[0].elements.map((e) => e.id)).toEqual(['el-1']);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('ghost'));
    warn.mockRestore();
  });

  it('states an empty panel rather than leaving the last one standing', () => {
    mount();
    deliver(ready(), upsert('el-1', 'cards'), {
      t: 'sidebar.state',
      slots: [{ id: 'cards', elementIds: ['el-1'] }],
      active: 'cards',
      visible: true,
      rev: 4
    });

    deliver({ t: 'sidebar.state' });

    expect(panel).toEqual({ slots: [], active: null, visible: false, rev: 0 });
  });
});

describe('the mobile rule', () => {
  const raise = (extra: Record<string, unknown> = {}) =>
    deliver({
      t: 'sidebar.state',
      slots: [{ id: 'cards', elementIds: ['el-1'] }],
      active: 'cards',
      visible: true,
      rev: 4,
      ...extra
    } as ServerMsg);

  const restoreVisible = () => {
    deliver(ready(), upsert('el-1', 'cards'));
    raise();
  };

  it('puts the panel away on a phone that has just reloaded', () => {
    // The panel is a 95%-wide sheet there, and a reload that put it straight
    // back would cover the question the user reloaded to answer.
    widen(390);
    mount();

    restoreVisible();

    expect(panel.visible).toBe(false);
    // The slots are untouched: hidden, not thrown away, and the tab the
    // server chose is selected -- a button in the feed leads to a ready tab.
    expect(panel.slots.map((s) => s.id)).toEqual(['cards']);
    expect(panel.active).toBe('cards');
    // And the server is told, because it must never branch on the device
    // itself.
    expect(sent).toContainEqual({ t: 'sidebar.user', op: 'hide', rev: 4 });
  });

  it('leaves a wide viewport alone', () => {
    widen(1280);
    mount();

    restoreVisible();

    expect(panel.visible).toBe(true);
    expect(sent).toEqual([]);
  });

  it('declines a raise that arrives mid-conversation too', () => {
    // The scenario that finished while the user was reading the feed. Same
    // sheet, same feed underneath it, same answer -- the panel is filled and
    // the tab selected, and the user opens it when they choose to.
    widen(390);
    mount();
    deliver(ready(), upsert('el-1', 'cards'));
    raise();
    act(() => dispatch({ op: 'hide' }));
    sent.length = 0;

    raise({ rev: 5 });

    expect(panel.visible).toBe(false);
    expect(panel.active).toBe('cards');
    expect(sent).toContainEqual({ t: 'sidebar.user', op: 'hide', rev: 5 });
  });

  it('leaves a panel that is already on screen where it is', () => {
    // The rule is about the transition, not the level. A second slot filled
    // while the user has the panel open restates `visible: true`, and
    // slamming it shut under their thumb is the opposite of the point.
    widen(390);
    mount();
    deliver(ready(), upsert('el-1', 'cards'));
    act(() => dispatch({ op: 'show' }));
    sent.length = 0;

    raise({ rev: 5 });

    expect(panel.visible).toBe(true);
    expect(sent).toEqual([]);
  });

  it('raises it anyway when the server says it means it', () => {
    // `Sidebar.show()` and `set_slot(activate="force")`: the application has
    // said this must be seen wherever it lands.
    widen(390);
    mount();
    deliver(ready(), upsert('el-1', 'cards'));

    raise({ force: true });

    expect(panel.visible).toBe(true);
    expect(sent).toEqual([]);
  });
});

describe('the revision a client quotes', () => {
  it('is the one the last frame carried, never one of its own', () => {
    // It addresses the server's states. A local guess that moved it would
    // claim the client had seen something nobody sent, and the server would
    // fall silent on an operation that needed answering.
    mount();
    deliver(ready(), upsert('el-1', 'cards'), {
      t: 'sidebar.state',
      slots: [{ id: 'cards', elementIds: ['el-1'] }],
      active: 'cards',
      visible: true,
      rev: 4
    });

    act(() => dispatch({ op: 'hide' }));
    act(() => dispatch({ op: 'show' }));

    expect(panel.rev).toBe(4);
    expect(sent).toEqual([
      { t: 'sidebar.user', op: 'hide', rev: 4 },
      { t: 'sidebar.user', op: 'show', rev: 4 }
    ]);
  });

  it('follows the next frame', () => {
    mount();
    deliver(ready(), {
      t: 'sidebar.state',
      slots: [],
      active: null,
      visible: false,
      rev: 11
    });

    act(() => dispatch({ op: 'show' }));

    expect(sent).toEqual([{ t: 'sidebar.user', op: 'show', rev: 11 }]);
  });
});

describe('useElementSidebar dispatch', () => {
  it('applies the operation and tells the server', () => {
    mount();
    deliver(ready(), upsert('el-1', 'cards'), {
      t: 'sidebar.state',
      slots: [{ id: 'cards', elementIds: ['el-1'] }],
      active: 'cards',
      visible: true,
      rev: 4
    });

    act(() => dispatch({ op: 'hide' }));

    expect(panel.visible).toBe(false);
    expect(sent).toEqual([{ t: 'sidebar.user', op: 'hide', rev: 4 }]);
  });

  it('names the slot on a structural operation', () => {
    mount();
    deliver(ready(), upsert('el-1', 'cards'), {
      t: 'sidebar.state',
      slots: [{ id: 'cards', elementIds: ['el-1'] }],
      active: 'cards',
      visible: true,
      rev: 4
    });

    act(() => dispatch({ op: 'close', slot: 'cards' }));

    expect(panel.slots).toEqual([]);
    expect(sent).toEqual([
      { t: 'sidebar.user', op: 'close', slot: 'cards', rev: 4 }
    ]);
  });

  it('keeps a local operation local', () => {
    // What `ReadOnlyThread` uses: its elements come from a REST record of a
    // thread the live session has never been in, so the server would refuse
    // the preview and the refusal would wipe it.
    mount();
    deliver(ready());
    const element = { id: 'rest-1', name: 'the report' } as never;

    act(() => dispatch({ op: 'preview', element }, { local: true }));

    expect(panel.slots.map((s) => s.id)).toEqual(['preview']);
    expect(sent).toEqual([]);
  });
});
