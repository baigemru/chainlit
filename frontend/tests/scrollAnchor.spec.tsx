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
