import { render } from '@testing-library/react';
import { MessageContext, defaultMessageContext } from 'contexts/MessageContext';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createContext } from 'react';
import { RecoilRoot, atom } from 'recoil';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { IAction } from '@chainlit/react-client';

import MessageActions from '@/components/chat/Messages/Message/Buttons/Actions';

const HERE = dirname(fileURLToPath(import.meta.url));

// The package's atoms have to be built by the same `recoil` the `RecoilRoot`
// here comes from; the built bundle carries its own copy, and an atom from
// that one is not an atom to this store. Only three names are read by what
// is under test, and `MOBILE_BREAKPOINT` is one of them: `hooks/use-mobile`
// takes the phone rule from the package so the layout and the element panel
// cannot drift apart.
vi.mock('@chainlit/react-client', () => ({
  MOBILE_BREAKPOINT: 768,
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path,
    callAction: async () => undefined
  }),
  sessionIdState: atom<string | undefined>({
    key: 'ActionVariantsSessionId',
    default: 'session-1'
  })
}));

/**
 * What `Action.variant` buys the application, read off the render.
 *
 * The variant is a weight, so the assertions are about the classes the
 * theme keys off — an accented button, an outlined one, a pill — and about
 * where each kind of button lands: the chips a line of questions first (one
 * sideways strip on a phone), the commands a row under them (two to a line
 * on a phone).
 */
const action = (id: string, variant?: IAction['variant']): IAction =>
  ({ id, name: id, label: id, forId: 'm1', payload: {}, variant }) as any;

const setWidth = (width: number) => {
  Object.defineProperty(window, 'innerWidth', {
    value: width,
    configurable: true,
    writable: true
  });
};

const renderActions = (actions: IAction[]) =>
  render(
    <RecoilRoot>
      <MessageContext.Provider value={defaultMessageContext}>
        <MessageActions actions={actions} />
      </MessageContext.Provider>
    </RecoilRoot>
  );

const classesOf = (id: string) =>
  (document.getElementById(id) as HTMLElement).className;

afterEach(() => setWidth(1024));

describe('action variants', () => {
  it('draws the accented button for the one thing to do next', () => {
    renderActions([action('primary', 'primary')]);

    const classes = classesOf('primary');
    expect(classes).toContain('bg-primary');
    // The trap this guards: `cn` is tailwind-merge and the class list the
    // button is handed wins over its variant's, so a stray
    // `text-muted-foreground` would overwrite the only colour that makes
    // the accented button readable.
    expect(classes).not.toContain('text-muted-foreground');
    expect(classes).toContain('text-primary-foreground');
  });

  it('outlines the alternative to it', () => {
    renderActions([action('secondary', 'secondary')]);

    const classes = classesOf('secondary');
    // Outlined, with the line the text-sized step gives an outline.
    expect(classes).toContain('border-muted-foreground/40');
    expect(classes).not.toContain('bg-primary');
  });

  it('sizes the commands by their text, the chip by its own classes', () => {
    // The mockup's `.b`: a two-word label sat afloat in shadcn's 36px box.
    renderActions([
      action('go', 'primary'),
      action('alt', 'secondary'),
      action('quiet'),
      action('weight', 'chip')
    ]);

    for (const id of ['go', 'alt', 'quiet']) {
      const classes = classesOf(id).split(/\s+/);
      expect(classes).toContain('py-[5px]');
      expect(classes).toContain('text-xs');
      expect(classes).not.toContain('h-9');
    }
    expect(classesOf('weight').split(/\s+/)).toContain('h-7');
  });

  it('draws a chip as a pill, not as a button', () => {
    renderActions([action('chip', 'chip')]);

    const classes = classesOf('chip');
    expect(classes).toContain('rounded-full');
    expect(classes).toContain('text-xs');
    expect(classes).not.toContain('bg-primary');
  });

  it('leaves an action that names no variant exactly as it was', () => {
    renderActions([action('quiet'), action('explicit', 'default')]);

    for (const id of ['quiet', 'explicit']) {
      const classes = classesOf(id);
      expect(classes).toContain('text-muted-foreground');
      expect(classes).not.toContain('bg-primary');
      expect(classes).not.toContain('border-input');
      expect(classes).not.toContain('border-muted-foreground/40');
      expect(classes).not.toContain('rounded-full');
    }
  });

  it('keeps the chips in a line of their own, away from the commands', () => {
    renderActions([
      action('go', 'primary'),
      action('weight', 'chip'),
      action('again')
    ]);

    const commands = document.querySelector<HTMLElement>(
      '[data-role="message-actions"]'
    )!;
    const chips = document.querySelector<HTMLElement>(
      '[data-role="message-action-chips"]'
    )!;
    expect(Array.from(commands.children).map((c) => c.id)).toEqual([
      'go',
      'again'
    ]);
    expect(Array.from(chips.children).map((c) => c.id)).toEqual(['weight']);
    expect(chips.className).toContain('w-full');
  });

  it('puts the chips before the commands', () => {
    // A chip refines the answer just read; a command leaves it. Declared in
    // the other order on purpose: the rows are the component's, not the
    // application's list order.
    const { container } = renderActions([
      action('go', 'primary'),
      action('again'),
      action('weight', 'chip')
    ]);

    expect(
      Array.from(container.querySelectorAll('[data-role]')).map((node) =>
        node.getAttribute('data-role')
      )
    ).toEqual(['message-action-chips', 'message-actions']);
  });

  it('lets the chips wrap on a wide screen, with no fade', () => {
    renderActions([action('weight', 'chip'), action('box', 'chip')]);

    const chips = document.querySelector<HTMLElement>(
      '[data-role="message-action-chips"]'
    )!;
    expect(chips.className).toContain('flex-wrap');
    expect(chips.className).not.toContain('chip-strip');
  });

  it('draws no empty row when there are only chips', () => {
    renderActions([action('weight', 'chip')]);

    expect(document.querySelector('[data-role="message-actions"]')).toBeNull();
    expect(
      document.querySelector('[data-role="message-action-chips"]')
    ).not.toBeNull();
  });
});

describe('action layout on a phone', () => {
  it('puts the commands two to a line instead of in a column', () => {
    setWidth(375);
    renderActions([
      action('a', 'primary'),
      action('b'),
      action('c'),
      action('weight', 'chip')
    ]);

    const commands = document.querySelector<HTMLElement>(
      '[data-role="message-actions"]'
    )!;
    expect(commands.className).toContain('grid-cols-2');
    expect(commands.className).not.toContain('flex-col');
    // A chip stretched across half the width is a button again.
    const chips = document.querySelector<HTMLElement>(
      '[data-role="message-action-chips"]'
    )!;
    expect(chips.className).not.toContain('grid-cols-2');
    expect(classesOf('weight')).not.toContain('w-full');
    expect(classesOf('a')).toContain('w-full');
  });

  it('puts the chips first here too, as one strip that scrolls sideways', () => {
    setWidth(375);
    const { container } = renderActions([
      action('a', 'primary'),
      action('weight', 'chip'),
      action('box', 'chip')
    ]);

    expect(
      Array.from(container.querySelectorAll('[data-role]')).map((node) =>
        node.getAttribute('data-role')
      )
    ).toEqual(['message-action-chips', 'message-actions']);
    const chips = document.querySelector<HTMLElement>(
      '[data-role="message-action-chips"]'
    )!;
    // The one strip class the account's sections use too; what it draws
    // is pinned below, against the stylesheet.
    expect(chips.className).toContain('chip-strip');
    expect(chips.className).not.toContain('flex-wrap');
  });

  it('draws the strip as one line that scrolls, fades at its edge and does not squeeze', () => {
    // jsdom applies no stylesheet, so the rule is read where it is written:
    // a class that only exists in a component's `className` draws nothing.
    const css = readFileSync(resolve(HERE, '../src/index.css'), 'utf8');
    const rule = (selector: string) =>
      css.split(`${selector} {`)[1]?.split('}')[0].replace(/\s+/g, ' ') ?? '';

    expect(rule('.chip-strip')).toContain('flex-wrap: nowrap');
    expect(rule('.chip-strip')).toContain('overflow-x: auto');
    expect(rule('.chip-strip')).toContain('scrollbar-width: none');
    expect(rule('.chip-strip')).toContain(
      'mask-image: linear-gradient(90deg, #000 86%, transparent)'
    );
    expect(rule('.chip-strip > *')).toContain('flex-shrink: 0');
  });

  it('keeps the wide screen a wrapping row', () => {
    renderActions([action('a', 'primary'), action('b')]);

    const commands = document.querySelector<HTMLElement>(
      '[data-role="message-actions"]'
    )!;
    expect(commands.className).toContain('flex-wrap');
    expect(commands.className).not.toContain('grid-cols-2');
    expect(classesOf('a')).not.toContain('w-full');
  });
});
