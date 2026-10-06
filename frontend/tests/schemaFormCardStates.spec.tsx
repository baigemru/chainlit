import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm, { SchemaSection } from '@/components/SchemaForm';
import { fillLabel } from '@/components/SchemaForm/Cards';

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

beforeAll(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

/**
 * A card that knows what state it is in: a subscription that is on trial,
 * waiting for its payment, or cancelled offers different buttons, says so
 * with a coloured chip, and puts the amount due in the button that pays it.
 *
 * The schema is the shape `msgspec.json.schema` gives a `list[Subscription]`
 * with `Meta(extra_json_schema=...)` on the list and on the status field:
 * the `x-` keys ride beside `$ref` and `enum`, which is where the resolver
 * has to find them.
 */
const SCHEMA: IJsonSchema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        subs: { title: 'Подписки', $ref: '#/$defs/Subs' }
      },
      required: []
    },
    Subs: {
      title: 'Subs',
      type: 'object',
      properties: {
        items: {
          title: 'Подписки',
          'x-widget': 'cards',
          'x-actions-field': 'actions',
          'x-actions': [
            {
              name: 'pay',
              label: 'Оплатить · {due_label}',
              variant: 'primary'
            },
            { name: 'renew', label: 'Продлить' },
            { name: 'cancel', label: 'Отменить', variant: 'ghost' }
          ],
          type: 'array',
          items: { $ref: '#/$defs/Subscription' },
          default: []
        }
      },
      required: []
    },
    Subscription: {
      title: 'Subscription',
      type: 'object',
      properties: {
        title: { 'x-widget': 'title', type: 'string', default: '' },
        status: {
          title: 'Статус',
          'x-widget': 'badge',
          'x-enum-labels': {
            trial: 'Пробный',
            pending: 'Ждёт оплаты',
            active: 'Активна',
            cancelled: 'Отменена'
          },
          'x-tones': {
            pending: 'warning',
            active: 'success',
            cancelled: 'danger',
            trial: 'sparkly'
          },
          enum: ['active', 'cancelled', 'pending', 'trial', ''],
          default: ''
        },
        due_label: { 'x-widget': 'hidden', type: 'string', default: '' },
        actions: {
          'x-widget': 'hidden',
          type: 'array',
          items: { type: 'string' },
          default: []
        }
      },
      required: []
    }
  }
};

const sub = (over: Record<string, unknown>) => ({
  title: 'Поиск',
  status: 'active',
  due_label: '',
  actions: [],
  ...over
});

const mount = (items: Record<string, unknown>[], schema = SCHEMA) => {
  const onAction = vi.fn(() => Promise.resolve());
  render(
    <SchemaForm
      schema={schema}
      values={{ subs: { items } }}
      onSubmit={vi.fn(() => Promise.resolve({}))}
      onAction={onAction}
    >
      <SchemaSection name="subs" />
    </SchemaForm>
  );
  return { onAction };
};

const cards = () => screen.getAllByTestId('account-card');

const buttonsOf = (card: HTMLElement) =>
  Array.from(card.querySelectorAll('button')).map((b) => b.textContent);

describe('a card in a state', () => {
  it('offers only the actions its field names, in `x-actions` order', () => {
    mount([
      sub({ actions: ['cancel', 'pay'], due_label: '990 ₽' }),
      sub({ actions: ['renew'] }),
      sub({ actions: [] })
    ]);

    const [pending, lapsed, cancelled] = cards();
    expect(buttonsOf(pending)).toEqual(['Оплатить · 990 ₽', 'Отменить']);
    expect(buttonsOf(lapsed)).toEqual(['Продлить']);
    expect(buttonsOf(cancelled)).toEqual([]);
  });

  it('offers nothing when the card opted in but its field is not a list', () => {
    mount([sub({ actions: null })]);

    expect(buttonsOf(cards()[0])).toEqual([]);
  });

  it('offers every action when the list names no field', () => {
    const items = (SCHEMA.$defs as Record<string, IJsonSchema>).Subs.properties!
      .items;
    const { 'x-actions-field': _dropped, ...withoutField } = items;
    const schema: IJsonSchema = {
      ...SCHEMA,
      $defs: {
        ...SCHEMA.$defs,
        Subs: {
          ...(SCHEMA.$defs as Record<string, IJsonSchema>).Subs,
          properties: { items: withoutField }
        }
      }
    };
    mount([sub({ actions: [] })], schema);

    expect(buttonsOf(cards()[0])).toEqual(['Оплатить', 'Продлить', 'Отменить']);
  });

  it('sends the pressed action of the filtered set with its card', async () => {
    const { onAction } = mount([
      sub({ actions: ['renew'] }),
      sub({ actions: ['pay'], due_label: '490 ₽' })
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Оплатить · 490 ₽' }));

    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    expect(onAction.mock.calls[0]).toEqual([
      'pay',
      'subs.items.1',
      sub({ actions: ['pay'], due_label: '490 ₽' })
    ]);
  });

  it('draws a value as text, never as markup', () => {
    mount([sub({ actions: ['pay'], due_label: '<b>990</b>' })]);

    const button = screen.getByRole('button', {
      name: 'Оплатить · <b>990</b>'
    });
    expect(button.querySelector('b')).toBeNull();
  });

  it('weighs each button by its variant', () => {
    mount([sub({ actions: ['pay', 'renew', 'cancel'], due_label: '1 ₽' })]);

    const pay = screen.getByRole('button', { name: 'Оплатить · 1 ₽' });
    const renew = screen.getByRole('button', { name: 'Продлить' });
    const cancel = screen.getByRole('button', { name: 'Отменить' });

    expect(pay.className).toContain('bg-primary');
    expect(pay.className).not.toContain('border-input');
    // No variant is the outlined button every action had before the key.
    expect(renew.className).toContain('border-input');
    expect(renew.className).not.toContain('bg-primary');
    expect(cancel.className).toContain('hover:bg-accent');
    expect(cancel.className).not.toContain('border-input');
    expect(cancel.className).not.toContain('bg-primary');
  });

  it('puts the status chip beside the title, in its label and tone', () => {
    mount([
      sub({ status: 'pending' }),
      sub({ status: 'active' }),
      sub({ status: 'cancelled' })
    ]);

    const chips = screen.getAllByTestId('account-card-badge');
    expect(chips.map((chip) => chip.textContent)).toEqual([
      'Ждёт оплаты',
      'Активна',
      'Отменена'
    ]);
    expect(chips.map((chip) => chip.dataset.tone)).toEqual([
      'warning',
      'success',
      'danger'
    ]);
    expect(chips[1].className).toContain('bg-success/15');
    expect(chips[1].className).toContain('text-success');
    // The header row: the chip and the title share a parent.
    expect(chips[0].parentElement?.textContent).toContain('Поиск');
    // A chip, not a chip and a read-out of the same value under it.
    expect(screen.queryByText('Статус:')).toBeNull();
  });

  it('falls back to muted for a tone it has no colour for', () => {
    mount([sub({ status: 'trial' })]);

    const chip = screen.getByTestId('account-card-badge');
    expect(chip.textContent).toBe('Пробный');
    expect(chip.dataset.tone).toBe('muted');
    expect(chip.className).toContain('bg-muted');
  });

  it('draws no chip for an empty status and no "Статус:" read-out either', () => {
    mount([sub({ status: '' })]);

    expect(screen.queryByTestId('account-card-badge')).toBeNull();
    expect(screen.queryByText('Статус:')).toBeNull();
  });
});

describe('fillLabel', () => {
  it('fills a placeholder from the card', () => {
    expect(fillLabel('Оплатить · {due}', { due: '990 ₽' })).toBe(
      'Оплатить · 990 ₽'
    );
    expect(fillLabel('{n} дней', { n: 30 })).toBe('30 дней');
  });

  it('removes an empty placeholder with the separator before it', () => {
    expect(fillLabel('Оплатить · {due}', { due: '' })).toBe('Оплатить');
    expect(fillLabel('Оплатить · {due}', {})).toBe('Оплатить');
    expect(fillLabel('Оплатить: {due}', null)).toBe('Оплатить');
  });

  it('trims a separator a leading empty placeholder leaves behind', () => {
    expect(fillLabel('{due} · Оплатить', {})).toBe('Оплатить');
    expect(fillLabel('Оплатить {due} сейчас', {})).toBe('Оплатить сейчас');
  });

  it('puts no object into a label and leaves a label without placeholders alone', () => {
    expect(fillLabel('Оплатить · {due}', { due: { a: 1 } })).toBe('Оплатить');
    expect(fillLabel('— Отменить —', {})).toBe('— Отменить —');
    expect(fillLabel('Тариф {constructor}', {})).toBe('Тариф');
  });
});
