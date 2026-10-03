import { act, fireEvent, render } from '@testing-library/react';
import { Profiler } from 'react';
import { RecoilRoot, useSetRecoilState } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type IAsk,
  type IElementSidebarSlot,
  type IElementSidebarState,
  type IMessageElement,
  type IStep,
  askUserState,
  messagesState
} from '@chainlit/react-client';

import ElementSideView from '@/components/ElementSideView';
import { ResizablePanelGroup } from '@/components/ui/resizable';

/**
 * On a phone the panel is a modal sheet, and its overlay covers the feed —
 * which is where an ask puts its question and its buttons. Most asks wait
 * behind a sheet the user raised; an ask whose message is anchored to the
 * top (`metadata.anchor === "top"`) has claimed the screen, and the sheet
 * gives way to it: closed while it stands, back on the same tab when it
 * ends, and the server never told `hide` — a hide would leave the panel
 * shut after the answer, which is the whole difference between "outranked"
 * and "dismissed".
 */

const mockIsMobile = vi.fn();
vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => mockIsMobile()
}));

vi.mock('@/components/Elements', () => ({
  Element: ({ element }: { element: IMessageElement }) => (
    <div data-element-id={element.id}>{element.name}</div>
  )
}));

const mockDispatch = vi.fn();
let state: IElementSidebarState;
// The ask and the transcript are the real atoms, set through Recoil: which
// message an ask belongs to is the subject, and so is what the panel is
// subscribed to.
let ask: IAsk | undefined;
let messages: IStep[];
// From the package's source, not its build: the build resolves `recoil`
// from its own `node_modules`, and an atom registered with that copy is
// missing from this `RecoilRoot`.
vi.mock('@chainlit/react-client', async () => ({
  ...(await vi.importActual<typeof import('../../libs/react-client/src/state')>(
    '../../libs/react-client/src/state'
  )),
  ...(await vi.importActual<
    typeof import('../../libs/react-client/src/utils/message')
  >('../../libs/react-client/src/utils/message')),
  useElementSidebar: () => ({ state, dispatch: mockDispatch }),
  useFreshSlots: () => new Set<string>()
}));

const slot = (id: string): IElementSidebarSlot => ({
  id,
  title: id,
  elements: [
    {
      id: `${id}-el`,
      name: id,
      type: 'text',
      display: 'side'
    } as IMessageElement
  ],
  closable: true,
  canvas: false
});

const anAsk = (): IAsk =>
  ({
    callback: () => {},
    spec: { type: 'action', stepId: 'step-1', timeout: 60, keys: [] }
  }) as unknown as IAsk;

// The asking message, nested under a run the way a step's children are, so
// a lookup that only read the top level would miss it.
const asking = (metadata: Record<string, unknown>): IStep[] => [
  {
    id: 'run',
    type: 'run',
    steps: [{ id: 'step-1', type: 'assistant_message', metadata }]
  } as unknown as IStep
];

// The store's setters, captured from inside the root, so a test can move
// the ask or the transcript the way the session would.
let setAsk: (ask: IAsk | undefined) => void;
let setMessages: (messages: IStep[]) => void;
const Store = () => {
  setAsk = useSetRecoilState(askUserState);
  setMessages = useSetRecoilState(messagesState);
  return null;
};

// One call per commit in which the panel rendered.
const panelRenders = vi.fn();

const tree = () => (
  <RecoilRoot
    initializeState={({ set }) => {
      set(askUserState, ask);
      set(messagesState, messages);
    }}
  >
    <Store />
    <ResizablePanelGroup direction="horizontal">
      <Profiler id="panel" onRender={panelRenders}>
        <ElementSideView />
      </Profiler>
    </ResizablePanelGroup>
  </RecoilRoot>
);

const dialog = () => document.querySelector('[role="dialog"]');
const body = (id: string) =>
  document.querySelector(`[data-slot-id="${id}"]`) as HTMLElement | null;
const hides = () => mockDispatch.mock.calls.filter(([op]) => op?.op === 'hide');

beforeEach(() => {
  vi.clearAllMocks();
  ask = undefined;
  messages = asking({ anchor: 'top' });
  mockIsMobile.mockReturnValue(true);
  state = {
    slots: [slot('cards'), slot('report')],
    active: 'report',
    visible: true,
    rev: 1
  };
});

describe('the side sheet on a phone during an ask', () => {
  it('stays open over an ask that did not claim the screen', () => {
    messages = asking({});
    ask = anAsk();

    render(tree());

    expect(dialog()).not.toBeNull();
    expect(body('report')!.hidden).toBe(false);
  });

  it('stays open over another message anchored to the top', () => {
    // The anchor that counts is the asking message's, not any message's.
    messages = [
      ...asking({}),
      { id: 'other', type: 'assistant_message', metadata: { anchor: 'top' } }
    ] as unknown as IStep[];
    ask = anAsk();

    render(tree());

    expect(dialog()).not.toBeNull();
  });

  it('is closed while an ask anchored to the top stands', () => {
    ask = anAsk();

    render(tree());

    expect(dialog()).toBeNull();
    expect(body('report')).toBeNull();
  });

  it('closes when an ask arrives over an open sheet, without telling the server', () => {
    render(tree());
    expect(dialog()).not.toBeNull();

    act(() => setAsk(anAsk()));

    expect(dialog()).toBeNull();
    expect(hides()).toEqual([]);
  });

  it('comes back on the same tab once the ask ends, still without a hide', () => {
    ask = anAsk();
    render(tree());
    expect(dialog()).toBeNull();

    act(() => setAsk(undefined));

    expect(dialog()).not.toBeNull();
    expect(body('report')!.hidden).toBe(false);
    expect(body('cards')!.hidden).toBe(true);
    expect(hides()).toEqual([]);
  });

  it('still hides on the user’s own close', () => {
    // The prop now varies; the user's way out must not have gone with it.
    render(tree());

    fireEvent.click(
      Array.from(dialog()!.querySelectorAll('button')).find(
        (b) => b.textContent === 'Close'
      )!
    );

    expect(hides()).toHaveLength(1);
  });
});

describe('the side panel on a desktop during an ask', () => {
  it('stays as it was', () => {
    mockIsMobile.mockReturnValue(false);
    ask = anAsk();

    render(tree());

    expect(dialog()).toBeNull();
    expect(document.querySelector('aside')).not.toBeNull();
    expect(body('report')!.hidden).toBe(false);
    expect(hides()).toEqual([]);
  });
});

/**
 * The panel holds every slot and every custom element in them, and a
 * streaming answer rewrites the transcript on each `step.update`. Whether an
 * ask claims the screen is one boolean; the panel must re-render when that
 * flips, not whenever the feed moves.
 */
describe('the side panel and the feed', () => {
  const feedMoves = () => {
    for (let i = 0; i < 3; i++) {
      act(() =>
        setMessages([
          ...asking({ anchor: 'top' }),
          { id: `m${i}`, type: 'assistant_message', output: `${i}` }
        ] as unknown as IStep[])
      );
    }
  };

  it('does not re-render while no ask stands', () => {
    render(tree());
    const before = panelRenders.mock.calls.length;

    feedMoves();

    expect(panelRenders.mock.calls.length).toBe(before);
    expect(dialog()).not.toBeNull();
  });

  it('does not re-render under a standing ask whose answer stays the same', () => {
    ask = anAsk();
    render(tree());
    expect(dialog()).toBeNull();
    const before = panelRenders.mock.calls.length;

    feedMoves();

    expect(panelRenders.mock.calls.length).toBe(before);
  });

  it('re-renders when the asking message gains the anchor', () => {
    messages = asking({});
    ask = anAsk();
    render(tree());
    expect(dialog()).not.toBeNull();

    act(() => setMessages(asking({ anchor: 'top' })));

    expect(dialog()).toBeNull();
    expect(hides()).toEqual([]);
  });
});
