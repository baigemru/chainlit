import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm, {
  SchemaSection,
  SchemaSubmit,
  useSchemaForm
} from '@/components/SchemaForm';
import { ActionButtons } from '@/components/SchemaForm/Cards';

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

vi.mock('@/components/Markdown', () => ({
  Markdown: ({ children }: { children: string }) => (
    <div data-testid="markdown">{children}</div>
  )
}));

beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  Element.prototype.scrollIntoView = vi.fn();
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(() => {
  vi.restoreAllMocks();
});

/**
 * `msgspec.json.schema` output from the contract's `Watch` / `WatchedItem`
 * pair, generated on 19.09.2026 by msgspec 0.21.1 and pasted verbatim. The
 * `nested` field is this spec's own addition: the contract says a Struct
 * inside a card is not drawn, and there has to be one to not draw.
 */
const SCHEMA: IJsonSchema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        watch: {
          title: 'Слежение',
          'x-actions': [
            { name: 'recheck', label: 'Проверить всё', icon: 'refresh-cw' }
          ],
          $ref: '#/$defs/Watch'
        }
      },
      required: []
    },
    Watch: {
      title: 'Watch',
      description: 'Мои товары',
      type: 'object',
      properties: {
        items: {
          title: 'Товары',
          description: 'Пока пусто: добавьте товар из чата.',
          'x-widget': 'cards',
          'x-actions': [
            { name: 'compare', label: 'Где дешевле', icon: 'search' }
          ],
          type: 'array',
          items: { $ref: '#/$defs/WatchedItem' },
          default: []
        }
      },
      required: []
    },
    WatchedItem: {
      title: 'WatchedItem',
      type: 'object',
      properties: {
        image: { 'x-widget': 'image', type: 'string', default: '' },
        title: { 'x-widget': 'title', type: 'string', default: '' },
        price: { title: 'Цена', type: 'string', default: '' },
        watch: { title: 'Следить', type: 'boolean', default: false },
        seen: {
          title: 'Видели',
          readOnly: true,
          type: 'boolean',
          default: false
        },
        tags: { type: 'array', items: { type: 'string' }, default: [] },
        source: {
          title: 'Открыть на 1688',
          readOnly: true,
          'x-widget': 'link',
          type: 'string',
          default: ''
        },
        diff: {
          readOnly: true,
          'x-widget': 'markdown',
          type: 'string',
          default: ''
        },
        nested: { title: 'Вложенное', $ref: '#/$defs/Inner' }
      },
      required: []
    },
    Inner: {
      title: 'Inner',
      type: 'object',
      properties: { depth: { type: 'integer', default: 1 } },
      required: []
    }
  }
};

const item = (index: number) => ({
  image: `https://example.test/${index}.jpg`,
  title: `Товар ${index}`,
  price: `${index}00 ₽`,
  watch: false,
  seen: index === 0,
  tags: ['1688', 'опт'],
  source: `https://example.test/offer/${index}`,
  diff: `# Разница ${index}`,
  nested: { depth: index }
});

const values = (count: number) => ({
  watch: { items: Array.from({ length: count }, (_, index) => item(index)) }
});

/**
 * The section's own `x-actions` row, composed the way the dialog composes it:
 * out of `useSchemaForm()`, above the fields rather than inside them. The
 * form stopped drawing this when it stopped being a layout.
 */
const SectionActions = () => {
  const { form, onAction } = useSchemaForm();
  const tab = form.tabs[0];
  return (
    <ActionButtons
      actions={tab.actions}
      path={tab.name}
      item={null}
      onAction={onAction}
    />
  );
};

const mount = (
  count: number,
  overrides: Partial<Omit<Parameters<typeof SchemaForm>[0], 'children'>> = {}
) => {
  const onSubmit = vi.fn(() => Promise.resolve());
  const onAction = vi.fn(() => Promise.resolve());
  const utils = render(
    <SchemaForm
      schema={SCHEMA}
      values={values(count)}
      onSubmit={onSubmit}
      onAction={onAction}
      {...overrides}
    >
      <SectionActions />
      <SchemaSection name="watch" />
      <SchemaSubmit />
    </SchemaForm>
  );
  return { onSubmit, onAction, ...utils };
};

const cards = () => screen.queryAllByTestId('account-card');

describe('SchemaForm cards', () => {
  it('shows only the description for an empty list', () => {
    mount(0);

    expect(cards()).toHaveLength(0);
    expect(
      screen.getByText('Пока пусто: добавьте товар из чата.')
    ).toBeInTheDocument();
  });

  it('draws one card per element, image, title and badges included', () => {
    mount(1);

    expect(cards()).toHaveLength(1);
    const image = screen.getByRole('img', { name: 'Товар 0' });
    expect(image).toHaveAttribute('src', 'https://example.test/0.jpg');
    expect(image).toHaveClass('object-cover');
    expect(screen.getByText('Товар 0')).toBeInTheDocument();
    expect(screen.getByText('Цена:')).toBeInTheDocument();
    expect(screen.getByText('000 ₽')).toBeInTheDocument();
    expect(screen.getByText('1688')).toBeInTheDocument();
    expect(screen.getByText('опт')).toBeInTheDocument();
    // readOnly bool is a read-out, not a control: `✓` because `seen` is true.
    expect(screen.getByText('Видели:')).toBeInTheDocument();
    expect(screen.getByText('✓')).toBeInTheDocument();
    // The two kinds the card borrows from the rest of the form.
    expect(
      screen.getByRole('link', { name: 'Открыть на 1688' })
    ).toHaveAttribute('href', 'https://example.test/offer/0');
    expect(screen.getByTestId('markdown')).toHaveTextContent('# Разница 0');
  });

  it('scales to twenty cards and stacks them in one column below 640px', () => {
    mount(20);

    expect(cards()).toHaveLength(20);
    expect(screen.getAllByLabelText('Следить')).toHaveLength(20);
    expect(screen.getAllByRole('button', { name: 'Где дешевле' })).toHaveLength(
      20
    );
    // jsdom lays nothing out, so the column count is only assertable as the
    // classes Tailwind compiles it from.
    expect(cards()[0].parentElement).toHaveClass(
      'grid-cols-1',
      'sm:grid-cols-2'
    );
  });

  it('binds the switch to `<field>.<index>.<name>` and submits every element unchanged', async () => {
    const { onSubmit } = mount(2);
    const switches = screen.getAllByLabelText('Следить');

    fireEvent.click(switches[1]);
    expect(switches[1]).toHaveAttribute('data-state', 'checked');
    expect(switches[0]).toHaveAttribute('data-state', 'unchecked');

    fireEvent.click(screen.getByText('account.actions.save'));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toEqual({
      watch: {
        items: [item(0), { ...item(1), watch: true }]
      }
    });
  });

  it('sends a card action the element path and the value the form holds now', async () => {
    const { onAction } = mount(3);

    fireEvent.click(screen.getAllByLabelText('Следить')[2]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Где дешевле' })[2]);

    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    expect(onAction.mock.calls[0]).toEqual([
      'compare',
      'watch.items.2',
      { ...item(2), watch: true }
    ]);
  });

  it('holds the pressed button until the action settles', async () => {
    let release: () => void = () => {};
    const onAction = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        })
    );
    mount(1, { onAction });
    const button = screen
      .getByRole('button', { name: 'Где дешевле' })
      .closest('button')!;

    fireEvent.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('account.actions.working')).toBeInTheDocument();

    release();
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it('gives the button back when the action rejects, without an unhandled rejection', async () => {
    const onAction = vi.fn(() => Promise.reject(new Error('nope')));
    const escaped: unknown[] = [];
    const collect = (reason: unknown) => escaped.push(reason);
    process.on('unhandledRejection', collect);

    mount(1, { onAction });
    const button = screen
      .getByRole('button', { name: 'Где дешевле' })
      .closest('button')!;
    fireEvent.click(button);

    await waitFor(() => expect(onAction).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 10));
    process.off('unhandledRejection', collect);
    expect(escaped).toEqual([]);
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it('does not submit the form when a card button is pressed', async () => {
    const { onSubmit, onAction } = mount(1);

    fireEvent.click(screen.getByRole('button', { name: 'Где дешевле' }));

    await waitFor(() => expect(onAction).toHaveBeenCalled());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('refuses to draw a nested object, says so once, and still submits it', async () => {
    const errors: string[] = [];
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
      errors.push(String(args[0]));
    });

    const { onSubmit } = mount(3);

    // One line for the field, not one per card.
    const mine = errors.filter((line) => line.includes('watch.items.*.nested'));
    expect(mine).toHaveLength(1);
    expect(screen.queryByText('Вложенное:')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('account.actions.save'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const saved = onSubmit.mock.calls[0][0] as unknown as {
      watch: { items: { nested: unknown }[] };
    };
    expect(saved.watch.items[2].nested).toEqual({ depth: 2 });
  });

  it('calls a section action with the section name and no item', async () => {
    const { onAction } = mount(1);

    fireEvent.click(screen.getByRole('button', { name: 'Проверить всё' }));

    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    expect(onAction.mock.calls[0]).toEqual(['recheck', 'watch', null]);
  });
});
