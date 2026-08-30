import { render } from '@testing-library/react';
import { MutableRefObject, ReactNode } from 'react';
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import ScrollContainer from '@/components/chat/ScrollContainer';

const mockUseChatMessages = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useChatMessages: () => mockUseChatMessages()
}));

const CONTAINER_HEIGHT = 800;
const SCROLL_HEIGHT = 5000;

const scrollTo = vi.fn();
const scrollTopWrites: number[] = [];

/**
 * jsdom has no layout: every offset is 0 and `scrollTo` does not exist, so the
 * container's arithmetic would be invisible and every assertion below would
 * pass for the wrong reason. The geometry therefore comes from data attributes
 * the cases set on their own children, and the two scroll paths — the smooth
 * `scrollTo` to an anchor and the bare `scrollTop = scrollHeight` bottom-follow
 * — are recorded separately, because telling them apart is the whole point.
 */
const patched: Array<[string, PropertyDescriptor | undefined]> = [];

const patch = (name: string, descriptor: PropertyDescriptor) => {
  patched.push([
    name,
    Object.getOwnPropertyDescriptor(HTMLElement.prototype, name)
  ]);
  Object.defineProperty(HTMLElement.prototype, name, {
    configurable: true,
    ...descriptor
  });
};

const fromData = (key: string) =>
  function (this: HTMLElement) {
    return Number(this.dataset[key] ?? 0);
  };

patch('offsetHeight', { get: fromData('offsetHeight') });
patch('offsetTop', { get: fromData('offsetTop') });
// Only the scroll container's clientHeight is ever read.
patch('clientHeight', { get: () => CONTAINER_HEIGHT });
patch('scrollHeight', { get: () => SCROLL_HEIGHT });
patch('scrollTo', {
  writable: true,
  value: (...args: unknown[]) => scrollTo(...args)
});
patch('scrollTop', {
  get(this: HTMLElement & { _scrollTop?: number }) {
    return this._scrollTop ?? 0;
  },
  set(this: HTMLElement & { _scrollTop?: number }, value: number) {
    this._scrollTop = value;
    scrollTopWrites.push(value);
  }
});

afterAll(() => {
  for (const [name, descriptor] of patched) {
    if (descriptor) {
      Object.defineProperty(HTMLElement.prototype, name, descriptor);
    } else {
      delete (HTMLElement.prototype as unknown as Record<string, unknown>)[
        name
      ];
    }
  }
});

beforeEach(() => {
  vi.clearAllMocks();
  scrollTopWrites.length = 0;
  mockUseChatMessages.mockReturnValue({ messages: [] });
});

const USER_TOP = 100;
const ASSISTANT_TOP = 400;

const userMessage = (
  <div
    key="user"
    data-step-type="user_message"
    data-offset-top={USER_TOP}
    data-offset-height={60}
  />
);

const assistantMessage = (height: number, nested = false) => (
  <div
    key="assistant"
    data-step-type="assistant_message"
    data-offset-top={ASSISTANT_TOP}
    data-offset-height={height}
  >
    {nested ? (
      <div
        data-step-type="assistant_message"
        data-offset-top={ASSISTANT_TOP + 20}
        data-offset-height={height - 20}
      />
    ) : null}
  </div>
);

const messagesOfLength = (length: number) =>
  Array.from({ length }, (_, index) => ({ id: `m${index}` }));

const renderContainer = (
  anchor: 'bottom' | 'top' | undefined,
  children: ReactNode,
  autoScrollRef: MutableRefObject<boolean>
) =>
  render(
    <ScrollContainer
      autoScrollUserMessage
      autoScrollAssistantMessage
      assistantMessageAnchor={anchor}
      autoScrollRef={autoScrollRef}
    >
      {children}
    </ScrollContainer>
  );

const scrollToTargets = () =>
  scrollTo.mock.calls.map((call) => (call[0] as { top: number }).top);

const CARDS_TOP = 400;
const CARDS_HEIGHT = 300;
const OFFER_TOP = 900;
const OFFER_HEIGHT = 120;

/**
 * A message the application marked from Python (`cl.Message(anchor=...)`).
 * The flag rides in the step metadata and reaches the DOM as `data-anchor`
 * on the message's root element — the only thing the container can see.
 */
const markedMessage = (
  key: string,
  anchor: 'top' | 'bottom' | 'none' | undefined,
  top: number,
  height: number
) => (
  <div
    key={key}
    data-step-type="assistant_message"
    data-anchor={anchor}
    data-offset-top={top}
    data-offset-height={height}
  />
);

const container = (
  anchor: 'bottom' | 'top',
  children: ReactNode,
  autoScrollRef: MutableRefObject<boolean>
) => (
  <ScrollContainer
    autoScrollUserMessage
    autoScrollAssistantMessage
    assistantMessageAnchor={anchor}
    autoScrollRef={autoScrollRef}
  >
    {children}
  </ScrollContainer>
);

describe('ScrollContainer assistant anchor', () => {
  it('follows the stream to the bottom by default', () => {
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(1) });
    const view = renderContainer('bottom', userMessage, autoScrollRef);

    // The reply arrives: with no anchor mode the user message keeps the
    // anchor and the view is dragged to the end of the stream.
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      <ScrollContainer
        autoScrollUserMessage
        autoScrollAssistantMessage
        assistantMessageAnchor="bottom"
        autoScrollRef={autoScrollRef}
      >
        {[userMessage, assistantMessage(300)]}
      </ScrollContainer>
    );

    expect(scrollTopWrites).toContain(SCROLL_HEIGHT);
    expect(scrollToTargets()).not.toContain(ASSISTANT_TOP - 20);
  });

  it('brings a new assistant message to the top', () => {
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(1) });
    const view = renderContainer('top', userMessage, autoScrollRef);

    expect(scrollToTargets()).toEqual([USER_TOP - 20]);

    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      <ScrollContainer
        autoScrollUserMessage
        autoScrollAssistantMessage
        assistantMessageAnchor="top"
        autoScrollRef={autoScrollRef}
      >
        {[userMessage, assistantMessage(300)]}
      </ScrollContainer>
    );

    expect(scrollToTargets()).toEqual([USER_TOP - 20, ASSISTANT_TOP - 20]);
  });

  it('does not chase the same message as it streams', () => {
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    const view = renderContainer(
      'top',
      [userMessage, assistantMessage(300)],
      autoScrollRef
    );

    expect(scrollToTargets()).toEqual([ASSISTANT_TOP - 20]);

    // A token lands: the same element, a new `messages` array.
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      <ScrollContainer
        autoScrollUserMessage
        autoScrollAssistantMessage
        assistantMessageAnchor="top"
        autoScrollRef={autoScrollRef}
      >
        {[userMessage, assistantMessage(700)]}
      </ScrollContainer>
    );

    expect(scrollToTargets()).toEqual([ASSISTANT_TOP - 20]);
  });

  it('anchors on the message, not on a step nested inside it', () => {
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    renderContainer(
      'top',
      [userMessage, assistantMessage(300, true)],
      autoScrollRef
    );

    expect(scrollToTargets()).toEqual([ASSISTANT_TOP - 20]);
  });

  it('never drags the view to the bottom', () => {
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(1) });
    const view = renderContainer('top', userMessage, autoScrollRef);

    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      <ScrollContainer
        autoScrollUserMessage
        autoScrollAssistantMessage
        assistantMessageAnchor="top"
        autoScrollRef={autoScrollRef}
      >
        {[userMessage, assistantMessage(300)]}
      </ScrollContainer>
    );

    expect(scrollTopWrites).toEqual([]);
  });

  it('never drags the view to the bottom without an anchor either', () => {
    // An empty thread nulls the anchor, and the resize effect's initial pass
    // then reaches the second bottom-follow assignment — the one the anchored
    // branch never shadows.
    mockUseChatMessages.mockReturnValue({ messages: [] });
    renderContainer('top', <div />, { current: true });

    expect(scrollTopWrites).toEqual([]);
  });

  it('sizes the spacer so the anchor can sit at the top', () => {
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    const { container } = renderContainer(
      'top',
      [userMessage, assistantMessage(300)],
      autoScrollRef
    );

    const spacer = container.querySelector('.flex-shrink-0') as HTMLElement;
    expect(spacer.style.height).toBe(`${CONTAINER_HEIGHT - 300 - 32}px`);
  });
});

describe('ScrollContainer per-message anchor', () => {
  // Every case below opens the thread with the user's message and then
  // forgets what that first render did: mounting is two passes of
  // `updateSpacerHeight` (the messages effect and the resize effect's initial
  // call), and in bottom mode both reach the scroll. What is under test here
  // is only what the *next* message does.
  const openThread = (
    mode: 'bottom' | 'top',
    autoScrollRef: MutableRefObject<boolean>
  ) => {
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(1) });
    // An array from the start: a single child swapped for an array remounts
    // the div, and a remounted user message is a new anchor identity.
    const view = render(container(mode, [userMessage], autoScrollRef));
    scrollTo.mockClear();
    scrollTopWrites.length = 0;
    return view;
  };

  it('leaves the view on the cards when the offer refuses the anchor', () => {
    // The owner's case: a feed of product cards, then "want me to analyse
    // these?". The offer must land after the cards without moving the view.
    const autoScrollRef = { current: true };
    const view = openThread('top', autoScrollRef);

    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      container(
        'top',
        [
          userMessage,
          markedMessage('cards', undefined, CARDS_TOP, CARDS_HEIGHT)
        ],
        autoScrollRef
      )
    );

    expect(scrollToTargets()).toEqual([CARDS_TOP - 20]);

    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(3) });
    view.rerender(
      container(
        'top',
        [
          userMessage,
          markedMessage('cards', undefined, CARDS_TOP, CARDS_HEIGHT),
          markedMessage('offer', 'none', OFFER_TOP, OFFER_HEIGHT)
        ],
        autoScrollRef
      )
    );

    // The cards keep the anchor: no scroll to the offer, and no follow to the
    // end either.
    expect(scrollToTargets()).toEqual([CARDS_TOP - 20]);
    expect(scrollTopWrites).toEqual([]);

    // The offer is still counted as height after the anchor, so the spacer
    // shrinks by exactly its height rather than pretending it is not there.
    const spacer = view.container.querySelector(
      '.flex-shrink-0'
    ) as HTMLElement;
    expect(spacer.style.height).toBe(
      `${CONTAINER_HEIGHT - CARDS_HEIGHT - OFFER_HEIGHT - 32}px`
    );
  });

  it('pins a message that asks for the top even in bottom mode', () => {
    const autoScrollRef = { current: true };
    const view = openThread('bottom', autoScrollRef);

    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      container(
        'bottom',
        [userMessage, markedMessage('cards', 'top', CARDS_TOP, CARDS_HEIGHT)],
        autoScrollRef
      )
    );

    expect(scrollToTargets()).toEqual([CARDS_TOP - 20]);
    expect(scrollTopWrites).toEqual([]);

    // The offer lands after the pinned cards. Appearing at the end of the
    // thread is not what earns a message the pin — holding the anchor is — so
    // the mode's bottom-follow must stay off now that there is height after
    // it, and the smooth scroll must not be re-issued.
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(3) });
    view.rerender(
      container(
        'bottom',
        [
          userMessage,
          markedMessage('cards', 'top', CARDS_TOP, CARDS_HEIGHT),
          markedMessage('offer', undefined, OFFER_TOP, OFFER_HEIGHT)
        ],
        autoScrollRef
      )
    );

    expect(scrollToTargets()).toEqual([CARDS_TOP - 20]);
    expect(scrollTopWrites).toEqual([]);
  });

  it('follows a message that asks for the bottom, exactly once', () => {
    const autoScrollRef = { current: true };
    const view = openThread('top', autoScrollRef);

    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      container(
        'top',
        [
          userMessage,
          markedMessage('offer', 'bottom', OFFER_TOP, OFFER_HEIGHT)
        ],
        autoScrollRef
      )
    );

    expect(scrollTopWrites).toEqual([SCROLL_HEIGHT]);
    // It never becomes the anchor, so nothing smooth-scrolls to it.
    expect(scrollToTargets()).toEqual([]);

    // The same message, grown by a token: the view has already been dropped
    // to the end and must not be dragged there again.
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(2) });
    view.rerender(
      container(
        'top',
        [
          userMessage,
          markedMessage('offer', 'bottom', OFFER_TOP, OFFER_HEIGHT + 200)
        ],
        autoScrollRef
      )
    );

    expect(scrollTopWrites).toEqual([SCROLL_HEIGHT]);
    expect(scrollToTargets()).toEqual([]);
  });

  it('does not move at all for a lone message that refuses the anchor', () => {
    // Top mode is not incidental here: in bottom mode a null anchor lets the
    // resize effect's initial pass reach the bottom-follow assignment, so
    // "no scroll at all" is only true of the mode this case runs in.
    const autoScrollRef = { current: true };
    mockUseChatMessages.mockReturnValue({ messages: messagesOfLength(1) });
    render(
      container(
        'top',
        [markedMessage('offer', 'none', OFFER_TOP, OFFER_HEIGHT)],
        autoScrollRef
      )
    );

    expect(scrollTo).not.toHaveBeenCalled();
    expect(scrollTopWrites).toEqual([]);
  });
});
