import {
  act,
  fireEvent,
  render,
  screen,
  waitFor
} from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm, {
  LEADING,
  SchemaSection,
  SchemaSubmit,
  draftLeaves,
  hasEditable,
  resolveForm,
  searchFields,
  useSchemaForm
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
  // Radix's switch measures itself; jsdom has no observer to measure with.
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
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

/** What the form tells the layout, drawn where a spec can read it. */
const DirtyProbe = () => (
  <span data-testid="dirty">{String(useSchemaForm().isDirty)}</span>
);

const mount = (
  section: string,
  values: Record<string, unknown> = VALUES,
  schema: IJsonSchema = SCHEMA
) => {
  // The server stored what it was sent, unless a spec says otherwise.
  const onSubmit = vi.fn((sent: Record<string, unknown>) =>
    Promise.resolve<Record<string, unknown>>(sent)
  );
  const view = (next: Record<string, unknown>) => (
    <SchemaForm schema={schema} values={next} onSubmit={onSubmit}>
      <SchemaSection name={section} />
      <SchemaSubmit />
      <DirtyProbe />
    </SchemaForm>
  );
  const utils = render(view(values));
  return {
    onSubmit,
    arrive: (next: Record<string, unknown>) => utils.rerender(view(next)),
    ...utils
  };
};

/**
 * Until the save has resolved and the form has done what it does after it:
 * a draft typed while the answer is still out is what the answer overwrites.
 */
const settled = async (onSubmit: ReturnType<typeof vi.fn>) => {
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
  await act(async () => {
    await Promise.resolve(onSubmit.mock.results[0].value).catch(() => {});
  });
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

  it('adopts the answer to a save whole, with no page arriving', async () => {
    // The load hook may normalise what was saved; the form shows what is
    // stored, not what was typed. And it hears the answer from the save
    // itself: the dialog's SWR cache re-renders nothing for an answer equal
    // to what it holds.
    const { onSubmit } = mount('calc');
    onSubmit.mockResolvedValueOnce({ ...VALUES, calc: { margin: 30 } });

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    save();

    await waitFor(() => expect(screen.getByLabelText('Маржа')).toHaveValue(30));
    expect(screen.getByTestId('dirty')).toHaveTextContent('false');
  });

  it('keeps a draft typed after a save that changed nothing', async () => {
    // Save on a clean form: the answer is the page already held. The form
    // used to mark "the next page is the answer" and wait for one; none came,
    // and the section change after it reset over this draft.
    const { onSubmit, arrive } = mount('calc');

    save();
    await settled(onSubmit);
    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    // After a submit react-hook-form re-validates on change, which is
    // asynchronous: the draft is the form's only once that has run, and a
    // section change is a click away, not the same tick.
    await waitFor(() =>
      expect(screen.getByTestId('dirty')).toHaveTextContent('true')
    );
    arrive(later);

    expect(screen.getByLabelText('Маржа')).toHaveValue(35);
  });

  it('keeps the draft over a page that arrives after a refused save', async () => {
    // A refusal answers with no page, so nothing about the next arrival is
    // "the answer": it is a section change under a draft the user is about
    // to fix.
    const { onSubmit, arrive } = mount('calc');
    onSubmit.mockRejectedValueOnce(new Error('Bad Request'));

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    save();
    await settled(onSubmit);
    arrive(later);

    expect(screen.getByLabelText('Маржа')).toHaveValue(35);
    expect(screen.getByTestId('dirty')).toHaveTextContent('true');
  });

  it('reports a draft to the layout, and none once it is saved', async () => {
    mount('calc');
    expect(screen.getByTestId('dirty')).toHaveTextContent('false');

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    expect(screen.getByTestId('dirty')).toHaveTextContent('true');

    save();
    await waitFor(() =>
      expect(screen.getByTestId('dirty')).toHaveTextContent('false')
    );
  });
});

describe('a page that arrives under a draft, leaf by leaf', () => {
  // Siblings of the draft in the same section: what separates putting the
  // draft's leaves back from react-hook-form's `keepDirtyValues`. That one
  // keeps the whole dirty section (`set(values, "calc", old subtree)` in
  // `_reset`), and only a leaf registered on screen escapes it through the
  // `setValue` that runs first -- a hidden leaf, which never registers, gets
  // its old value back, and the save sends that as the user's edit over what
  // the application wrote since.
  const WITH_SIBLINGS: IJsonSchema = {
    ...SCHEMA,
    $defs: {
      ...SCHEMA.$defs,
      Calc: {
        ...SCHEMA.$defs!.Calc,
        properties: {
          margin: { title: 'Маржа', type: 'number', default: 20 },
          rate: { title: 'Курс', type: 'string', default: '' },
          rev: {
            title: 'Ревизия',
            'x-widget': 'hidden',
            type: 'string',
            default: ''
          } as IJsonSchema
        }
      }
    }
  };
  const before = { ...VALUES, calc: { margin: 20, rate: '11.0', rev: 'r-1' } };

  it('takes the new siblings, drawn or hidden, and keeps the drafted leaf', async () => {
    const { onSubmit, arrive } = mount('calc', before, WITH_SIBLINGS);

    fireEvent.change(screen.getByLabelText('Маржа'), {
      target: { value: '35' }
    });
    const next = { ...before, calc: { margin: 20, rate: '12.5', rev: 'r-2' } };
    arrive(next);

    expect(screen.getByLabelText('Маржа')).toHaveValue(35);
    expect(screen.getByLabelText('Курс')).toHaveValue('12.5');
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [values, base] = onSubmit.mock.calls[0] as unknown as [
      Record<string, any>,
      Record<string, any>
    ];
    expect(values.calc).toEqual({ margin: 35, rate: '12.5', rev: 'r-2' });
    expect(base).toBe(next);
  });
});

describe('a page that arrives under a draft in a list', () => {
  // The feed's entries with a switch on them: the one control a card draws.
  const WITH_SWITCH: IJsonSchema = {
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
  };
  const entry = { ...VALUES.feed.entries[0], watch: false };
  const before = { ...VALUES, feed: { ...VALUES.feed, entries: [entry] } };
  const flip = () => fireEvent.click(screen.getByRole('switch'));

  it('adopts a page whose list is unchanged, and keeps the draft in it', () => {
    // Positions still name the same cards, so the draft goes back by them
    // and the page -- an action's answer, a section change -- is not
    // dropped over it.
    const { arrive } = mount('feed', before, WITH_SWITCH);
    flip();
    const next = { ...before, feed: { ...before.feed, cursor: 'c-10' } };

    arrive(next);

    expect(screen.getByRole('switch')).toBeChecked();
    // The page was adopted: a Reset now goes back to it, not to `before`.
    fireEvent.click(
      screen.getByRole('button', { name: 'common.actions.reset' })
    );
    expect(screen.getByRole('switch')).not.toBeChecked();
    expect(screen.getByTestId('dirty')).toHaveTextContent('false');
  });

  it('keeps the old page when the list under the draft changed', async () => {
    const { onSubmit, arrive } = mount('feed', before, WITH_SWITCH);
    flip();

    // A new card at the top: `entries.0` is someone else now.
    arrive({
      ...before,
      feed: {
        ...before.feed,
        entries: [{ ...entry, title: 'Чайник', run_id: 'r-8' }, entry]
      }
    });

    expect(screen.getAllByRole('switch')).toHaveLength(1);
    expect(screen.getByRole('switch')).toBeChecked();
    save();
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const [, base] = onSubmit.mock.calls[0] as unknown as [
      Record<string, any>,
      Record<string, any>
    ];
    expect(base).toBe(before);
  });
});

describe('draftLeaves', () => {
  const dirty = {
    calc: { margin: true },
    feed: { entries: [false, { watch: true }] }
  };
  const page = { feed: { entries: [{ id: 'a' }, { id: 'b' }] } };

  it('lists the leaves of the dirty state as paths', () => {
    expect(
      draftLeaves({ calc: { margin: true }, notify: true, feed: {} }, {}, {})
    ).toEqual([['calc', 'margin'], ['notify']]);
  });

  it('follows a draft into a list the new page carries unchanged', () => {
    expect(draftLeaves(dirty, page, structuredClone(page))).toEqual([
      ['calc', 'margin'],
      ['feed', 'entries', '1', 'watch']
    ]);
  });

  it('refuses a draft inside a list that changed, whose positions are not identities', () => {
    expect(
      draftLeaves(dirty, page, {
        feed: { entries: [{ id: 'c' }, { id: 'a' }, { id: 'b' }] }
      })
    ).toBeNull();
  });
});
