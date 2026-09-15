import { describe, expect, it } from 'vitest';

import type { IElementSidebarState, IMessageElement } from '../src/types';
import { applySidebarOp } from '../src/useElementSidebar';

/**
 * The whole of the client's own opinion about the element panel.
 *
 * There is deliberately no second copy of the server's reducer here: the
 * server's answer on a structural operation is a full state and overwrites
 * whatever this produced. What this has to get right is only the frame the
 * user sees between their click and that answer — and, for a read-only
 * thread, the frame they see forever, because nothing answers there at all.
 */

const element = (id: string, name = id): IMessageElement =>
  ({ id, name, type: 'text', display: 'side' }) as IMessageElement;

const slot = (
  id: string,
  over: Partial<IElementSidebarState['slots'][0]> = {}
) => ({
  id,
  title: id,
  elements: [element(`${id}-el`)],
  closable: true,
  canvas: false,
  ...over
});

const panel = (
  over: Partial<IElementSidebarState> = {}
): IElementSidebarState => ({
  slots: [slot('cards'), slot('pinned', { closable: false })],
  active: 'cards',
  visible: true,
  rev: 3,
  ...over
});

describe('applySidebarOp', () => {
  it('hides and shows without touching the contents', () => {
    const hidden = applySidebarOp(panel(), { op: 'hide' });
    expect(hidden.visible).toBe(false);
    expect(hidden.slots).toHaveLength(2);
    expect(applySidebarOp(hidden, { op: 'show' }).visible).toBe(true);
  });

  it('shows an empty panel too', () => {
    // The composer's chevron is always there; an empty panel is what it
    // opens when nothing has been put in one.
    const empty: IElementSidebarState = {
      slots: [],
      active: null,
      visible: false,
      rev: 0
    };
    expect(applySidebarOp(empty, { op: 'show' })).toEqual({
      slots: [],
      active: null,
      visible: true,
      rev: 0
    });
  });

  it('activates a slot that exists and ignores one that does not', () => {
    expect(
      applySidebarOp(panel(), { op: 'activate', slot: 'pinned' }).active
    ).toBe('pinned');
    const state = panel();
    expect(applySidebarOp(state, { op: 'activate', slot: 'ghost' })).toBe(
      state
    );
  });

  it('closes a tab and moves off it', () => {
    const closed = applySidebarOp(panel(), { op: 'close', slot: 'cards' });
    expect(closed.slots.map((s) => s.id)).toEqual(['pinned']);
    // Never left pointing at a tab that is gone.
    expect(closed.active).toBe('pinned');
    expect(closed.visible).toBe(true);
  });

  it('lands on the neighbour, the way the server does', () => {
    // The optimistic frame and the state the server sends next have to
    // agree, or closing a middle tab flickers from the neighbour to the
    // first and back.
    const four = panel({
      slots: ['a', 'b', 'c', 'd'].map((id) => slot(id)),
      active: 'c'
    });
    expect(applySidebarOp(four, { op: 'close', slot: 'c' }).active).toBe('b');

    const first = panel({ slots: four.slots, active: 'a' });
    expect(applySidebarOp(first, { op: 'close', slot: 'a' }).active).toBe('b');
  });

  it('never moves the revision itself', () => {
    // It is the server's number, quoted back. A local guess that bumped it
    // would tell the server the client had seen a state nobody sent.
    const state = panel();
    for (const operation of [
      { op: 'hide' } as const,
      { op: 'show' } as const,
      { op: 'activate', slot: 'pinned' } as const,
      { op: 'close', slot: 'cards' } as const,
      { op: 'preview', element: element('el-9') } as const
    ]) {
      expect(applySidebarOp(state, operation).rev).toBe(state.rev);
    }
  });

  it('refuses to close a tab the application pinned', () => {
    const state = panel();
    expect(applySidebarOp(state, { op: 'close', slot: 'pinned' })).toBe(state);
  });

  it('puts the panel away with its last tab', () => {
    const one = panel({ slots: [slot('cards')], active: 'cards' });
    const closed = applySidebarOp(one, { op: 'close', slot: 'cards' });
    expect(closed.slots).toEqual([]);
    expect(closed.active).toBeNull();
    expect(closed.visible).toBe(false);
  });

  it('opens a preview beside what is already there', () => {
    const previewed = applySidebarOp(panel(), {
      op: 'preview',
      element: element('el-9', 'the report')
    });
    expect(previewed.slots.map((s) => s.id)).toEqual([
      'cards',
      'pinned',
      'preview'
    ]);
    expect(previewed.active).toBe('preview');
    // A click in the feed is a request to see something.
    expect(previewed.visible).toBe(true);
    const preview = previewed.slots[2];
    expect(preview.title).toBe('the report');
    expect(preview.elements.map((e) => e.id)).toEqual(['el-9']);
    expect(preview.closable).toBe(true);
  });

  it('replaces the preview rather than growing a row of them', () => {
    const first = applySidebarOp(panel(), {
      op: 'preview',
      element: element('el-9')
    });
    const second = applySidebarOp(first, {
      op: 'preview',
      element: element('el-10')
    });
    expect(second.slots.filter((s) => s.id === 'preview')).toHaveLength(1);
    expect(second.slots[2].elements.map((e) => e.id)).toEqual(['el-10']);
  });

  it('brings a hidden panel back on a preview', () => {
    const hidden = panel({ visible: false });
    expect(
      applySidebarOp(hidden, { op: 'preview', element: element('el-9') })
        .visible
    ).toBe(true);
  });
});
