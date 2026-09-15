import { fireEvent, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  IElementSidebarSlot,
  IElementSidebarState,
  IMessageElement
} from '@chainlit/react-client';

import ElementSideView from '@/components/ElementSideView';
import { ResizablePanelGroup } from '@/components/ui/resizable';

/**
 * The first tests this repository has on the element panel, and they exist
 * for one property above all: **switching tabs must not remount a slot**.
 * That is what the retired `key` field was really buying, by forbidding
 * updates instead of surviving them, and it is invisible to every assertion
 * that only looks at what is on screen. Hence the mount counter.
 */

const mockIsMobile = vi.fn();
vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => mockIsMobile()
}));

// PR-0 owns the real custom element; here it stands for "something with
// state". Mounts, not renders: a re-render is fine and expected, and it is
// the *remount* that would throw away whatever the element was holding.
const mounts: string[] = [];
vi.mock('@/components/Elements', async () => {
  const { useEffect } = await vi.importActual<typeof import('react')>('react');
  return {
    Element: ({ element }: { element: IMessageElement }) => {
      useEffect(() => {
        mounts.push(element.id);
      }, []);
      return <div data-element-id={element.id}>{element.name}</div>;
    }
  };
});

const mockDispatch = vi.fn();
let state: IElementSidebarState;
vi.mock('@chainlit/react-client', () => ({
  useElementSidebar: () => ({ state, dispatch: mockDispatch })
}));

const element = (id: string): IMessageElement =>
  ({ id, name: id, type: 'text', display: 'side' }) as IMessageElement;

const slot = (
  id: string,
  over: Partial<IElementSidebarSlot> = {}
): IElementSidebarSlot => ({
  id,
  title: id,
  elements: [element(`${id}-el`)],
  closable: true,
  canvas: false,
  ...over
});

// The desktop panel is a `ResizablePanel`, which throws outside a group.
// `Page.tsx` supplies one; here it is the harness.
const mount = () =>
  render(
    <ResizablePanelGroup direction="horizontal">
      <ElementSideView />
    </ResizablePanelGroup>
  );

const body = (id: string) =>
  document.querySelector(`[data-slot-id="${id}"]`) as HTMLElement | null;

beforeEach(() => {
  vi.clearAllMocks();
  mounts.length = 0;
  mockIsMobile.mockReturnValue(false);
  state = { slots: [slot('cards')], active: 'cards', visible: true, rev: 1 };
});

describe('ElementSideView', () => {
  it('draws no tab strip for a single slot, just the title', () => {
    mount();

    expect(document.querySelector('#side-view-tabs')).toBeNull();
    expect(document.querySelector('#side-view-title')!.textContent).toContain(
      'cards'
    );
  });

  it('draws a tab per slot once there is more than one', () => {
    state = {
      slots: [slot('cards'), slot('report')],
      active: 'cards',
      visible: true,
      rev: 1
    };

    mount();

    const triggers = document.querySelectorAll('#side-view-tabs [role="tab"]');
    expect(Array.from(triggers).map((t) => t.textContent)).toEqual([
      'cards',
      'report'
    ]);
  });

  it('keeps the inactive slot mounted and merely hidden', () => {
    // The whole point. A conditional render here would remount whatever the
    // inactive tab is holding every time the user looked away from it.
    state = {
      slots: [slot('cards'), slot('report')],
      active: 'cards',
      visible: true,
      rev: 1
    };

    const { rerender } = mount();
    const again = () =>
      rerender(
        <ResizablePanelGroup direction="horizontal">
          <ElementSideView />
        </ResizablePanelGroup>
      );

    // Mounted, contents and all, while it is the tab nobody is looking at.
    expect(body('report')!.hidden).toBe(true);
    expect(
      body('report')!.querySelector('[data-element-id="report-el"]')
    ).not.toBeNull();
    expect(body('cards')!.hidden).toBe(false);

    state = { ...state, active: 'report' };
    again();
    expect(body('cards')!.hidden).toBe(true);
    expect(body('report')!.hidden).toBe(false);

    // Back again, which is where a conditional render shows: the first tab
    // would have been unmounted while it was away and would mount a second
    // time here, with a fresh `useState` and nothing the user had typed.
    state = { ...state, active: 'cards' };
    again();
    expect(mounts).toEqual(['cards-el', 'report-el']);
  });

  it('asks the model to switch tabs rather than switching by itself', () => {
    state = {
      slots: [slot('cards'), slot('report')],
      active: 'cards',
      visible: true,
      rev: 1
    };

    mount();
    fireEvent.mouseDown(
      document.querySelectorAll('#side-view-tabs [role="tab"]')[1]
    );

    expect(mockDispatch).toHaveBeenCalledWith({
      op: 'activate',
      slot: 'report'
    });
  });

  it('offers a close button only on a tab that may be closed', () => {
    state = {
      slots: [slot('cards'), slot('pinned', { closable: false })],
      active: 'cards',
      visible: true,
      rev: 1
    };

    mount();

    const closers = document.querySelectorAll(
      '#side-view-tabs [data-close-slot]'
    );
    expect(closers).toHaveLength(1);
    expect(closers[0].getAttribute('aria-label')).toBe('Close cards');

    fireEvent.click(closers[0]);
    expect(mockDispatch).toHaveBeenCalledWith({ op: 'close', slot: 'cards' });
  });

  it('closes an inactive tab without first activating it', () => {
    // A sibling of the trigger, not a child: nested interactive content had
    // to fight the trigger off with `preventDefault`, which then also
    // swallowed the focus the trigger activates on.
    state = {
      slots: [slot('cards'), slot('report')],
      active: 'cards',
      visible: true,
      rev: 1
    };

    mount();
    const closer = document.querySelector(
      '#side-view-tabs [data-close-slot="report"]'
    )!;
    // The whole sequence a pointer makes: Radix's trigger activates on
    // `mousedown`, so a nested "×" would select the tab on the way down and
    // close it on the way up.
    fireEvent.pointerDown(closer);
    fireEvent.mouseDown(closer);
    fireEvent.click(closer);

    expect(mockDispatch).toHaveBeenCalledTimes(1);
    expect(mockDispatch).toHaveBeenCalledWith({ op: 'close', slot: 'report' });
  });

  it('lets a single slot be closed as well as hidden', () => {
    // The commonest state of the panel, and the one where "close" and
    // "hide" were indistinguishable: no strip, so the header carries it.
    mount();

    const closer = document.querySelector('#side-view-title [data-close-slot]');
    expect(closer).not.toBeNull();
    fireEvent.click(closer!);
    expect(mockDispatch).toHaveBeenCalledWith({ op: 'close', slot: 'cards' });

    // And the back arrow beside it still only hides.
    fireEvent.click(document.querySelector('#side-view-title button')!);
    expect(mockDispatch).toHaveBeenCalledWith({ op: 'hide' });
  });

  it('gives a slot the application pinned no close button at all', () => {
    state = {
      slots: [slot('pinned', { closable: false })],
      active: 'pinned',
      visible: true,
      rev: 1
    };

    mount();

    expect(document.querySelector('[data-close-slot]')).toBeNull();
  });

  it('keeps the tab strip beside a canvas slot', () => {
    // A canvas gives up its header, not the way out of itself: without the
    // strip the panel is one-way, and hiding and reopening it comes back to
    // the same canvas.
    state = {
      slots: [slot('board', { canvas: true }), slot('notes')],
      active: 'board',
      visible: true,
      rev: 1
    };

    mount();

    const triggers = document.querySelectorAll('#side-view-tabs [role="tab"]');
    expect(Array.from(triggers).map((t) => t.textContent)).toEqual([
      'board',
      'notes'
    ]);
    // The header is back too, because it is what carries the strip.
    expect(document.querySelector('#side-view-title')!.textContent).toContain(
      'board'
    );
  });

  it('hides rather than destroys when the back arrow is used', () => {
    mount();

    fireEvent.click(document.querySelector('#side-view-title button')!);

    expect(mockDispatch).toHaveBeenCalledWith({ op: 'hide' });
  });

  it('draws a canvas slot without a header when it is the whole panel', () => {
    state = {
      slots: [slot('board', { canvas: true })],
      active: 'board',
      visible: true,
      rev: 1
    };

    mount();

    // The frame is still there; the title text is not.
    expect(document.querySelector('#side-view-title')!.textContent).toBe('');
  });

  it('renders nothing at all while the panel is put away', () => {
    state = { ...state, visible: false };

    const { container } = mount();

    expect(container.querySelector('#side-view-content')).toBeNull();
    expect(container.querySelector('#side-view-title')).toBeNull();
  });

  it('renders an empty frame for an empty visible panel', () => {
    // Owner's decision: the chevron opens the panel whatever is in it, and
    // an empty frame is a truthful answer where a dead button is not.
    state = { slots: [], active: null, visible: true, rev: 1 };

    mount();

    expect(document.querySelector('#side-view-content')).not.toBeNull();
    expect(document.querySelector('#side-view-title')!.textContent).toBe('');
    expect(document.querySelectorAll('[data-slot-id]')).toHaveLength(0);
  });

  it('is a sheet on a phone, closing to a hide', () => {
    mockIsMobile.mockReturnValue(true);

    mount();

    const sheet = document.querySelector('[role="dialog"]');
    expect(sheet).not.toBeNull();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(mockDispatch).toHaveBeenCalledWith({ op: 'hide' });
  });
});
