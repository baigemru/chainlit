import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm from '@/components/SchemaForm';

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

// The real one reaches for `ChainlitContext` and drags the whole
// remark/rehype stack in; what this spec asks is only whether the markdown
// field renders its value instead of an input.
vi.mock('@/components/Markdown', () => ({
  Markdown: ({ children }: { children: string }) => (
    <div data-testid="markdown">{children}</div>
  )
}));

/**
 * Radix measures things jsdom cannot measure: `Select` scrolls its items into
 * view and captures the pointer, `Checkbox` sizes its hidden bubble input
 * through a `ResizeObserver`. All three are stubbed here rather than in
 * `setup-tests.ts`, which every other spec shares.
 */
beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  Element.prototype.scrollIntoView = vi.fn();
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const SCHEMA: IJsonSchema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        calc: { title: 'Расчёт', $ref: '#/$defs/Calc' },
        limits: { title: 'Лимиты', $ref: '#/$defs/Limits' },
        notify: { title: 'Уведомления', type: 'boolean', default: true },
        at: {
          title: 'Момент',
          type: 'string',
          format: 'date-time',
          default: '2026-09-19T12:00:00'
        },
        tags: {
          title: 'Метки',
          type: 'array',
          items: { type: 'string' },
          default: []
        },
        since: {
          title: 'С даты',
          anyOf: [{ type: 'string', format: 'date' }, { type: 'null' }],
          default: null
        },
        plan: {
          title: 'Тариф',
          readOnly: true,
          type: 'string',
          default: 'free'
        },
        kinds: {
          title: 'Виды',
          type: 'array',
          items: {
            enum: ['a', 'b'],
            'x-enum-labels': { a: 'Первый', b: 'Второй' }
          },
          default: []
        },
        blurb: {
          title: 'О тарифе',
          readOnly: true,
          'x-widget': 'markdown',
          type: 'string',
          default: ''
        },
        portal: {
          title: 'Платёжный кабинет',
          readOnly: true,
          'x-widget': 'link',
          type: 'string',
          default: '/billing/portal'
        },
        weird: {
          title: 'Странное',
          allOf: [{ type: 'object' }],
          default: {}
        }
      },
      required: []
    },
    Limits: {
      title: 'Limits',
      type: 'object',
      properties: {
        retries: { title: 'Повторы', type: 'integer', default: 3 },
        cap: {
          title: 'Потолок',
          anyOf: [{ type: 'integer' }, { type: 'null' }],
          default: null
        }
      },
      required: []
    },
    Calc: {
      title: 'Calc',
      description: 'Параметры расчёта',
      type: 'object',
      properties: {
        margin: {
          title: 'Маржа, %',
          'x-widget': 'slider',
          type: 'number',
          minimum: 0,
          maximum: 100,
          default: 20
        },
        currency: {
          title: 'Валюта',
          enum: ['CNY', 'RUB', 'USD'],
          'x-enum-labels': { CNY: 'Юань', RUB: 'Рубль', USD: 'Доллар' },
          default: 'USD'
        },
        note: {
          title: 'Заметка',
          'x-widget': 'textarea',
          anyOf: [{ type: 'string' }, { type: 'null' }],
          default: null
        }
      },
      required: []
    }
  }
};

const VALUES = () => ({
  calc: { margin: 20, currency: 'USD', note: 'привет' },
  limits: { retries: 3, cap: 10 },
  notify: true,
  at: '2026-09-19T12:00:00',
  tags: ['alpha'],
  since: '2026-09-19',
  plan: 'free',
  kinds: [],
  blurb: '# Тариф',
  portal: '/billing/portal',
  weird: { keep: 1 }
});

const mount = (overrides: Partial<Parameters<typeof SchemaForm>[0]> = {}) => {
  const onSubmit = vi.fn(() => Promise.resolve());
  const utils = render(
    <SchemaForm
      schema={SCHEMA}
      values={VALUES()}
      onSubmit={onSubmit}
      {...overrides}
    />
  );
  return { onSubmit, ...utils };
};

const openSelect = (trigger: HTMLElement) =>
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });

describe('SchemaForm', () => {
  it('draws the leading section above a tab strip', () => {
    mount();

    expect(screen.getByRole('tab', { name: 'Расчёт' })).toBeInTheDocument();
    expect(screen.getByText('Параметры расчёта')).toBeInTheDocument();
    // The non-object fields lead, with no invented heading over them.
    expect(screen.getByLabelText('Уведомления')).toBeInTheDocument();
    expect(screen.queryByText('General')).not.toBeInTheDocument();
  });

  it('toggles a switch', () => {
    mount();
    const notify = screen.getByLabelText('Уведомления');

    expect(notify).toHaveAttribute('data-state', 'checked');
    fireEvent.click(notify);
    expect(notify).toHaveAttribute('data-state', 'unchecked');
  });

  it('changes a select and labels its options with x-enum-labels', async () => {
    mount();
    const currency = screen.getByLabelText('Валюта');

    expect(currency).toHaveTextContent('Доллар');
    openSelect(currency);
    // By role, not by text: inside a form Radix also renders a hidden native
    // `<select>` carrying the very same option labels.
    fireEvent.click(await screen.findByRole('option', { name: 'Рубль' }));
    await waitFor(() => expect(currency).toHaveTextContent('Рубль'));
  });

  it('shows the slider value beside it', () => {
    mount();
    const margin = screen.getByLabelText('Маржа, %') as HTMLInputElement;

    expect(margin.type).toBe('range');
    expect(screen.getByText('20')).toBeInTheDocument();
    fireEvent.change(margin, { target: { value: '35' } });
    expect(screen.getByText('35')).toBeInTheDocument();
  });

  it('adds a tag on Enter and removes it on the badge cross', () => {
    mount();
    const draft = screen.getByLabelText('Метки');

    expect(screen.getByText('alpha')).toBeInTheDocument();
    fireEvent.change(draft, { target: { value: 'beta' } });
    // `false` means the keydown was cancelled. It has to be: a form whose only
    // text input is this one submits on Enter, so an unprevented Enter saves
    // the account instead of adding the tag.
    expect(fireEvent.keyDown(draft, { key: 'Enter' })).toBe(false);
    expect(screen.getByText('beta')).toBeInTheDocument();
    expect(draft).toHaveValue('');

    const crosses = screen.getAllByRole('button', { name: '×' });
    fireEvent.click(crosses[0]);
    expect(screen.queryByText('alpha')).not.toBeInTheDocument();
    expect(screen.getByText('beta')).toBeInTheDocument();
  });

  it('checks a multiselect box', () => {
    mount();
    const first = screen.getByLabelText('Первый');

    expect(first).toHaveAttribute('data-state', 'unchecked');
    fireEvent.click(first);
    expect(first).toHaveAttribute('data-state', 'checked');
  });

  it('carries the ISO value into a date input', () => {
    mount();
    const since = screen.getByLabelText('С даты') as HTMLInputElement;

    expect(since.type).toBe('date');
    expect(since.value).toBe('2026-09-19');
  });

  it('renders a markdown field instead of a control', () => {
    mount();

    expect(screen.getByTestId('markdown')).toHaveTextContent('# Тариф');
  });

  it('renders x-widget link as an anchor, not an input', () => {
    mount();
    const link = screen.getByRole('link', { name: 'Платёжный кабинет' });

    expect(link).toHaveAttribute('href', '/billing/portal');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(
      screen.queryByDisplayValue('/billing/portal')
    ).not.toBeInTheDocument();
  });

  it('shows an unsupported field as JSON and says so', () => {
    mount();

    expect(screen.getByText('account.unsupported')).toBeInTheDocument();
    expect(screen.getByText(/"keep": 1/)).toBeInTheDocument();
  });

  it('disables a readOnly field and the whole form when readonly', () => {
    const { unmount } = mount();
    expect(screen.getByLabelText('Тариф')).toBeDisabled();
    expect(screen.getByLabelText('Уведомления')).not.toBeDisabled();
    expect(screen.getByText('account.actions.save')).toBeInTheDocument();
    unmount();

    mount({ readonly: true });
    expect(screen.getByLabelText('Уведомления')).toBeDisabled();
    expect(screen.queryByText('account.actions.save')).not.toBeInTheDocument();
  });

  it('submits a nested object with the values it was given back', async () => {
    const { onSubmit } = mount();

    fireEvent.click(screen.getByLabelText('Уведомления'));
    fireEvent.change(screen.getByLabelText('Маржа, %'), {
      target: { value: '35' }
    });
    fireEvent.change(screen.getByLabelText('Заметка'), {
      target: { value: '' }
    });
    fireEvent.click(screen.getByLabelText('Второй'));
    fireEvent.change(screen.getByLabelText('Метки'), {
      target: { value: 'beta' }
    });
    fireEvent.keyDown(screen.getByLabelText('Метки'), { key: 'Enter' });
    // What a browser's `datetime-local` hands back when the seconds are zero.
    fireEvent.change(screen.getByLabelText('Момент'), {
      target: { value: '2026-09-20T15:30' }
    });
    fireEvent.change(screen.getByLabelText('Потолок'), {
      target: { value: '' }
    });

    fireEvent.click(screen.getByText('account.actions.save'));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toEqual({
      calc: { margin: 35, currency: 'USD', note: null },
      limits: { retries: 3, cap: null },
      notify: false,
      // msgspec decodes `datetime` as RFC 3339, which wants the seconds.
      at: '2026-09-20T15:30:00',
      tags: ['alpha', 'beta'],
      since: '2026-09-19',
      plan: 'free',
      kinds: ['b'],
      blurb: '# Тариф',
      portal: '/billing/portal',
      weird: { keep: 1 }
    });
  });

  it('hides the panel of every tab but the active one', () => {
    mount();
    const calc = screen
      .getByLabelText('Маржа, %')
      .closest('[role="tabpanel"]')!;
    const limits = screen
      .getByLabelText('Повторы')
      .closest('[role="tabpanel"]')!;

    expect(calc).not.toHaveAttribute('hidden');
    expect(limits).toHaveAttribute('hidden');

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Лимиты' }));
    expect(calc).toHaveAttribute('hidden');
    expect(limits).not.toHaveAttribute('hidden');
  });

  it('refuses to submit an emptied number that cannot be null', async () => {
    const { onSubmit } = mount();

    fireEvent.change(screen.getByLabelText('Повторы'), {
      target: { value: '' }
    });
    fireEvent.click(screen.getByText('account.actions.save'));

    expect(await screen.findByText('Required')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('restores the last accepted values on Reset', () => {
    mount();
    const notify = screen.getByLabelText('Уведомления');

    fireEvent.click(notify);
    expect(notify).toHaveAttribute('data-state', 'unchecked');

    fireEvent.click(screen.getByText('common.actions.reset'));
    expect(notify).toHaveAttribute('data-state', 'checked');
  });

  it('gives the Save button back when onSubmit rejects', async () => {
    const onSubmit = vi.fn(() => Promise.reject(new Error('nope')));
    // react-hook-form's `handleSubmit` rethrows after clearing `isSubmitting`,
    // and React drops the promise its submit handler returns: a refusal the
    // form does not catch leaves the page with an unhandled rejection.
    const escaped: unknown[] = [];
    const collect = (reason: unknown) => escaped.push(reason);
    process.on('unhandledRejection', collect);

    render(
      <SchemaForm schema={SCHEMA} values={VALUES()} onSubmit={onSubmit} />
    );
    const save = screen.getByText('account.actions.save').closest('button')!;

    fireEvent.click(save);

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 10));
    process.off('unhandledRejection', collect);
    expect(escaped).toEqual([]);
    await waitFor(() => expect(save).not.toBeDisabled());
  });
});
