import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm, {
  LEADING,
  SchemaSection,
  SchemaSubmit,
  draftLeaves,
  hasEditable,
  resolveForm,
  searchFields
} from '@/components/SchemaForm';

/**
 * `x-widget: "hidden"`, the section with nothing to save, and a page that
 * arrives under a draft.
 *
 * The three are one spec because they are one contract seen from three
 * sides: a hidden leaf is stored and never drawn, so a section made only of
 * hidden leaves and read-outs has nothing to save, and the page the dialog
 * asks for again on a section change must not take a draft with it.
 */

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  Element.prototype.scrollIntoView = vi.fn();
});

/**
 * `msgspec.json.schema` output, generated on 25.09.2026 by msgspec 0.21.1
 * from an `Account` with a hidden scalar at the top, a hidden Struct, and a
 * feed whose entries carry a hidden run id and a hidden "seen" -- the shape
 * the application's feed takes once the flag stops being a switch.
 */
const SCHEMA: IJsonSchema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        calc: { title: 'Расчёт', $ref: '#/$defs/Calc' },
        feed: { title: 'Лента', $ref: '#/$defs/Feed' },
        internal: {
          title: 'Служебное',
          'x-widget': 'hidden',
          $ref: '#/$defs/Internal'
        },
        pair_id: {
          title: 'ID пары',
          'x-widget': 'hidden',
          type: 'string',
          default: ''
        }
      },
      required: []
    },
    Calc: {
      title: 'Calc',
      description: 'Расчёт',
      type: 'object',
      properties: {
        margin: { title: 'Маржа', type: 'number', default: 20 }
      },
      required: []
    },
    Feed: {
      title: 'Feed',
      description: 'Лента',
      type: 'object',
      properties: {
        entries: {
          title: 'Записи',
          'x-widget': 'cards',
          type: 'array',
          items: { $ref: '#/$defs/Entry' },
          default: []
        },
        cursor: {
          title: 'Курсор',
          'x-widget': 'hidden',
          type: 'string',
          default: ''
        }
      },
      required: []
    },
    Entry: {
      title: 'Entry',
      type: 'object',
      properties: {
        title: { 'x-widget': 'title', type: 'string', default: '' },
        change: {
          title: 'Что изменилось',
          readOnly: true,
          type: 'string',
          default: ''
        },
        run_id: {
          title: 'Замер',
          'x-widget': 'hidden',
          type: 'string',
          default: ''
        },
        seen: {
          title: 'Просмотрено',
          'x-widget': 'hidden',
          type: 'boolean',
          default: false
        }
      },
      required: []
    },
    Internal: {
      title: 'Internal',
      type: 'object',
      properties: { run: { type: 'string', default: '' } },
      required: []
    }
  }
};

const VALUES = {
  calc: { margin: 20 },
  feed: {
    entries: [
      {
        title: 'Кружка',
        change: 'цена 11,8 → 12,4 ¥',
        run_id: 'r-7f3a',
        seen: false
      }
    ],
    cursor: 'c-9'
  },
  internal: { run: 'x' },
  pair_id: '859086919394'
};

describe('x-widget: hidden, resolved', () => {
  const form = resolveForm(SCHEMA);

  it('marks a hidden leaf, whatever its type', () => {
    expect(form.sections.map((field) => [field.name, field.kind])).toEqual([
      ['internal', 'hidden'],
      ['pair_id', 'hidden']
    ]);
    const entry = form.tabs
      .find((tab) => tab.name === 'feed')!
      .fields.find((field) => field.name === 'entries')!;
    expect(entry.itemFields!.map((field) => [field.name, field.kind])).toEqual([
      ['title', 'string'],
      ['change', 'string'],
      ['run_id', 'hidden'],
      ['seen', 'hidden']
    ]);
  });

  it('does not make a hidden Struct a section', () => {
    // A menu row onto nothing: `internal` stays a field so its value rides.
    expect(form.tabs.map((tab) => tab.name)).toEqual(['calc', 'feed']);
  });

  it('is not something a search can find', () => {
    expect(searchFields(form, 'ID пары')).toEqual([]);
    expect(searchFields(form, 'Курсор')).toEqual([]);
  });
});

describe('hasEditable', () => {
  const form = resolveForm(SCHEMA);
  const tab = (name: string) => form.tabs.find((t) => t.name === name)!.fields;

  it('finds the input in an ordinary section', () => {
    expect(hasEditable(tab('calc'))).toBe(true);
  });

  it('finds nothing in a feed of read-outs and hidden leaves', () => {
    expect(hasEditable(tab('feed'))).toBe(false);
  });

  it('finds nothing in a leading section made of hidden leaves', () => {
    expect(hasEditable(form.sections)).toBe(false);
  });

  it('counts a card list for the switch its cards carry', () => {
    const withSwitch = resolveForm({
      ...SCHEMA,
      $defs: {
        ...SCHEMA.$defs,
        Entry: {
          ...SCHEMA.$defs!.Entry,
          properties: {
            ...SCHEMA.$defs!.Entry.properties,
            watch: { title: 'Следить', type: 'boolean', default: false }
          }
        }
      }
    });

    expect(
      hasEditable(withSwitch.tabs.find((t) => t.name === 'feed')!.fields)
    ).toBe(true);
  });
});

const mount = (section: string, values: Record<string, unknown> = VALUES) => {
  const onSubmit = vi.fn(() => Promise.resolve());
  const view = (next: Record<string, unknown>) => (
    <SchemaForm schema={SCHEMA} values={next} onSubmit={onSubmit}>
      <SchemaSection name={section} />
      <SchemaSubmit />
    </SchemaForm>
  );
  const utils = render(view(values));
  return {
    onSubmit,
    arrive: (next: Record<string, unknown>) => utils.rerender(view(next)),
    ...utils
  };
};

const save = () =>
  fireEvent.click(screen.getByRole('button', { name: 'account.actions.save' }));

describe('x-widget: hidden, drawn', () => {
  it('prints nothing of a hidden leaf on a card', () => {
    mount('feed');

    expect(screen.getByText('Кружка')).toBeInTheDocument();
    expect(screen.getByText('цена 11,8 → 12,4 ¥')).toBeInTheDocument();
    expect(screen.queryByText(/Замер/)).not.toBeInTheDocument();
    expect(screen.queryByText('r-7f3a')).not.toBeInTheDocument();
    expect(screen.queryByText(/Просмотрено/)).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });

  it('draws nothing for the hidden leaves of a section', () => {
    const { container } = mount(LEADING);

    expect(container.querySelector('form')!.textContent).not.toMatch(
      /ID пары|859086919394|Служебное/
    );
  });

  it('sends every hidden value back with the save', async () => {
    const { onSubmit } = mount('calc');

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [values] = onSubmit.mock.calls[0] as unknown as [Record<string, any>];
    expect(values.calc.margin).toBe(35);
    expect(values.pair_id).toBe('859086919394');
    expect(values.internal).toEqual({ run: 'x' });
    expect(values.feed.cursor).toBe('c-9');
    expect(values.feed.entries[0]).toMatchObject({
      run_id: 'r-7f3a',
      seen: false
    });
  });
});

describe('a page that arrives under a draft', () => {
  const later = {
    ...VALUES,
    feed: { ...VALUES.feed, cursor: 'c-10' },
    pair_id: '777'
  };

  it('keeps the draft and adopts the rest, and the save is its difference', async () => {
    const { onSubmit, arrive } = mount('calc');

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    // The dialog asked again for another section while the draft was open.
    arrive(later);

    expect(screen.getByLabelText('Маржа')).toHaveValue(35);
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [values, base] = onSubmit.mock.calls[0] as unknown as [
      Record<string, any>,
      Record<string, any>
    ];
    expect(values.calc.margin).toBe(35);
    expect(values.pair_id).toBe('777');
    expect(values.feed.cursor).toBe('c-10');
    // `base` is the page that arrived: what the save claims is the draft.
    expect(base).toBe(later);
  });

  it('adopts a page whole when there is no draft', () => {
    const { arrive } = mount('calc');

    arrive({ ...later, calc: { margin: 42 } });

    expect(screen.getByLabelText('Маржа')).toHaveValue(42);
  });

  it('adopts the answer to a save whole, a draft or not', async () => {
    // The load hook may normalise what was saved; the form shows what is
    // stored, not what was typed.
    const { onSubmit, arrive } = mount('calc');

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    save();
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    arrive({ ...VALUES, calc: { margin: 30 } });

    expect(screen.getByLabelText('Маржа')).toHaveValue(30);
  });
});

describe('draftLeaves', () => {
  it('lists the leaves of the dirty state as paths', () => {
    expect(
      draftLeaves({ calc: { margin: true }, notify: true, feed: {} })
    ).toEqual([['calc', 'margin'], ['notify']]);
  });

  it('refuses a draft inside a list, whose positions are not identities', () => {
    expect(
      draftLeaves({
        calc: { margin: true },
        feed: { entries: [false, { watch: true }] }
      })
    ).toBeNull();
  });
});
