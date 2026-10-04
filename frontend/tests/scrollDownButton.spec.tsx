import { fireEvent, render, screen } from '@testing-library/react';
import { afterAll, describe, expect, it, vi } from 'vitest';

import ScrollContainer from '@/components/chat/ScrollContainer';

vi.mock('@chainlit/react-client', () => ({
  useChatMessages: () => ({ messages: [] })
}));

/**
 * jsdom has no layout, so "the arrow covers the form's button" cannot be
 * measured here. What can be checked is the structure that made it possible:
 * the arrow was absolutely positioned over the bottom of the scrolling feed,
 * and its full-width row swallowed every click on the last message beneath it
 * (seen live on the research launch form, 04.10.2026). An arrow that sits in
 * its own row after the feed cannot cover the feed at any scroll position.
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

// Mid-feed: 2000px of content, an 800px viewport, scrolled to the top.
patch('clientHeight', { get: () => 800 });
patch('scrollHeight', { get: () => 2000 });

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

describe('scroll-to-bottom arrow', () => {
  it('sits in its own row after the feed, never over it', () => {
    render(
      <ScrollContainer>
        <div data-testid="feed-content">
          <button>Начать исследование</button>
        </div>
      </ScrollContainer>
    );

    const content = screen.getByTestId('feed-content');
    const viewport = content.parentElement as HTMLElement;
    fireEvent.scroll(viewport);

    const arrow = document.getElementById('scroll-down-button') as HTMLElement;
    expect(arrow).not.toBeNull();
    const root = viewport.parentElement as HTMLElement;

    expect(viewport.contains(arrow)).toBe(false);
    expect(
      viewport.compareDocumentPosition(arrow) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();

    // Nothing between the arrow and the container lifts it out of the flow.
    for (let el: HTMLElement | null = arrow; el && el !== root; ) {
      expect(el.classList.contains('absolute')).toBe(false);
      expect(el.classList.contains('fixed')).toBe(false);
      el = el.parentElement;
    }
  });
});
