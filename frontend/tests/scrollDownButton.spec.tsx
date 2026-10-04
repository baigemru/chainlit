import { fireEvent, render, screen } from '@testing-library/react';
import { afterAll, describe, expect, it, vi } from 'vitest';

import ScrollContainer from '@/components/chat/ScrollContainer';

vi.mock('@chainlit/react-client', () => ({
  useChatMessages: () => ({ messages: [] })
}));

/**
 * jsdom has no layout, so "the arrow covers the form's button" cannot be
 * measured here; the structure that decides it can. The arrow used to float
 * centred under a full-width row that swallowed every click on the last
 * message beneath it (the research launch form, 04.10.2026). A row of its own
 * fixed that by cutting a strip off the feed, which the owner refused. What is
 * held here: it floats (the feed keeps its height), the row lets clicks
 * through and only the circle takes them. It stays centred: the owner refused
 * the corner too («словно уехала кнопка»).
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
  it('floats centred and takes clicks only on the circle', () => {
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
    const row = arrow.parentElement as HTMLElement;

    expect(viewport.contains(arrow)).toBe(false);

    // The feed is the only child in the flow: nothing reserves a row under it.
    for (const child of Array.from(root.children)) {
      if (child === viewport) continue;
      expect(child.classList.contains('absolute')).toBe(true);
    }

    expect(row.classList.contains('pointer-events-none')).toBe(true);
    expect(arrow.classList.contains('pointer-events-auto')).toBe(true);

    expect(row.classList.contains('justify-center')).toBe(true);
  });
});
