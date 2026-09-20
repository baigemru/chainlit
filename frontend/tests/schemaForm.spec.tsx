import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm, {
  LEADING,
  SchemaSection,
  SchemaSubmit,
  useSchemaForm
} from '@/components/SchemaForm';

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

/**
 * The layout the form no longer draws, spelled out the way the dialog does it:
 * the leading section, then every section, then the Save/Reset row. Sections
 * the dialog would keep off screen are all mounted here on purpose — one spec
 * should not have to click a menu to reach the field it is about.
 */
const mount = (
  overrides: Partial<Omit<Parameters<typeof SchemaForm>[0], 'children'>> = {}
) => {
  const onSubmit = vi.fn(() => Promise.resolve());
  const utils = render(
    <SchemaForm
      schema={SCHEMA}
      values={VALUES()}
      onSubmit={onSubmit}
      {...overrides}
    >
      <SchemaSection name={LEADING} />
      <SchemaSection name="calc" />
      <SchemaSection name="limits" />
      <SchemaSubmit />
    </SchemaForm>
  );
  return { onSubmit, ...utils };
};

const openSelect = (trigger: HTMLElement) =>
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });

describe('SchemaForm', () => {
  it('draws the fields of the sections the layout asked for, and no headings', () => {
    mount();

    // The leading scalars and a nested Struct's fields, addressed by name.
    expect(screen.getByLabelText('Уведомления')).toBeInTheDocument();
    expect(screen.getByLabelText('Маржа, %')).toBeInTheDocument();
    expect(screen.getByLabelText('Повторы')).toBeInTheDocument();
    // Fields only: the title, the description and the section's own actions
    // belong to whatever draws the header above them.
    expect(screen.queryByText('Расчёт')).not.toBeInTheDocument();
    expect(screen.queryByText('Параметры расчёта')).not.toBeInTheDocument();
    expect(screen.queryByText('General')).not.toBeInTheDocument();
  });

  it('draws nothing for a section the schema does not carry', () => {
    const { container } = render(
      <SchemaForm schema={SCHEMA} values={VALUES()} onSubmit={vi.fn()}>
        <SchemaSection name="gone" />
      </SchemaForm>
    );

    // A `?tab=` someone shared before the application renamed the Struct
    // field: nothing to draw is not a reason to take the dialog down.
    expect(container.querySelector('form')!.children).toHaveLength(0);
  });

  it('hands the layout the resolved schema and the submitting flag', () => {
    const seen: string[] = [];
    const Probe = () => {
      const { form, readonly, isSubmitting } = useSchemaForm();
      seen.push(
        `${form.tabs.map((tab) => tab.name).join(',')}|${readonly}|${isSubmitting}`
      );
      return null;
    };

    render(
      <SchemaForm schema={SCHEMA} values={VALUES()} onSubmit={vi.fn()}>
        <Probe />
      </SchemaForm>
    );

    // The section menu is drawn from this and nothing else.
    expect(seen[0]).toBe('calc,limits|false|false');
  });

  it('puts className on the form element', () => {
    const { container } = render(
      <SchemaForm
        schema={SCHEMA}
        values={VALUES()}
        onSubmit={vi.fn()}
        className="contents"
      >
        <SchemaSubmit />
      </SchemaForm>
    );

    expect(container.querySelector('form')).toHaveClass('contents');
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

  it('draws an image widget outside a card as the plain string field it is', () => {
    render(
      <SchemaForm
        schema={{
          $ref: '#/$defs/Root',
          $defs: {
            Root: {
              title: 'Root',
              type: 'object',
              properties: {
                cover: {
                  title: 'Обложка',
                  'x-widget': 'image',
                  type: 'string',
                  default: ''
                }
              },
              required: []
            }
          }
        }}
        values={{ cover: 'https://example.test/a.jpg' }}
        onSubmit={vi.fn()}
      >
        <SchemaSection name={LEADING} />
      </SchemaForm>
    );
    const cover = screen.getByLabelText('Обложка') as HTMLInputElement;

    // `image` and `title` are card vocabulary; a field that is not in a card
    // has to stay editable rather than turn into a picture of its own value.
    expect(cover.type).toBe('text');
    expect(cover.value).toBe('https://example.test/a.jpg');
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
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
      <SchemaForm schema={SCHEMA} values={VALUES()} onSubmit={onSubmit}>
        <SchemaSubmit />
      </SchemaForm>
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

/**
 * The resolver decides the order of an enum's options; these pin that the
 * three controls drawing one all take it from there and none re-sorts.
 */
describe('option order on screen', () => {
  const PROVINCES: IJsonSchema = {
    $ref: '#/$defs/Root',
    $defs: {
      Root: {
        title: 'Root',
        type: 'object',
        properties: {
          // As msgspec emits an Enum: sorted, which is how «не выбрана»
          // ends up between two province names.
          province: {
            title: 'Провинция',
            enum: ['guangdong', 'none', 'shandong', 'zhejiang'],
            'x-enum-labels': {
              none: 'не выбрана',
              shandong: 'Шаньдун',
              zhejiang: 'Чжэцзян'
            },
            default: 'none'
          },
          mode: {
            title: 'Режим',
            'x-widget': 'radio',
            enum: ['a', 'b', 'c'],
            'x-enum-labels': { c: 'Третий', a: 'Первый' },
            default: 'a'
          },
          kinds: {
            title: 'Виды',
            type: 'array',
            items: {
              enum: ['x', 'y', 'z'],
              'x-enum-labels': { z: 'Зет', x: 'Икс' }
            },
            default: []
          }
        },
        required: []
      }
    }
  };

  const mountProvinces = () =>
    render(
      <SchemaForm
        schema={PROVINCES}
        values={{ province: 'none', mode: 'a', kinds: [] }}
        onSubmit={vi.fn()}
      >
        <SchemaSection name={LEADING} />
      </SchemaForm>
    );

  it('lists a select in the declared order, unnamed values last', async () => {
    mountProvinces();
    openSelect(screen.getByLabelText('Провинция'));

    const options = await screen.findAllByRole('option');
    expect(options.map((option) => option.textContent)).toEqual([
      'не выбрана',
      'Шаньдун',
      'Чжэцзян',
      'guangdong'
    ]);
  });

  it('lists a radio group in the declared order', () => {
    const { container } = mountProvinces();
    const group = container.querySelector('[role="radiogroup"]')!;

    expect(
      Array.from(group.querySelectorAll('label')).map((label) =>
        label.textContent?.trim()
      )
    ).toEqual(['Третий', 'Первый', 'b']);
  });

  it('lists a multiselect in the declared order', () => {
    mountProvinces();
    // The box carries the value only in its `id`; the label beside it is
    // what a reader actually sees in that order.
    const boxes = screen.getAllByRole('checkbox');

    expect(boxes.map((box) => box.getAttribute('id'))).toEqual([
      'kinds.z',
      'kinds.x',
      'kinds.y'
    ]);
  });
});
