import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import SchemaForm, {
  LEADING,
  SchemaMatches,
  SchemaSubmit,
  resolveForm,
  searchFields
} from '@/components/SchemaForm';

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

/**
 * Two sections and a leading scalar, with the query words spread over titles,
 * descriptions and a group's children — the three places the search has to
 * look and the one place it has to look *through*.
 */
const SCHEMA: IJsonSchema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        notify: { title: 'Уведомления', type: 'boolean', default: true },
        email: {
          title: 'Почта',
          description: 'Куда слать уведомления',
          type: 'string',
          default: ''
        },
        calc: { title: 'Расчёт', $ref: '#/$defs/Calc' },
        watch: { title: 'Слежение', $ref: '#/$defs/Watch' }
      },
      required: []
    },
    Calc: {
      title: 'Calc',
      type: 'object',
      properties: {
        margin: { title: 'Маржа, %', type: 'number', default: 20 },
        currency: {
          title: 'Валюта',
          description: 'В чём считать',
          enum: ['CNY', 'USD'],
          default: 'USD'
        },
        limits: { title: 'Пределы', $ref: '#/$defs/Limits' }
      },
      required: []
    },
    Limits: {
      title: 'Limits',
      type: 'object',
      properties: {
        retries: { title: 'Повторы', type: 'integer', default: 3 },
        ceiling: { title: 'Потолок уведомлений', type: 'integer', default: 5 }
      },
      required: []
    },
    Watch: {
      title: 'Watch',
      type: 'object',
      properties: {
        period: { title: 'Период', type: 'integer', default: 7 }
      },
      required: []
    }
  }
};

const VALUES = () => ({
  notify: true,
  email: 'a@example.test',
  calc: { margin: 20, currency: 'USD', limits: { retries: 3, ceiling: 5 } },
  watch: { period: 7 }
});

const FORM = resolveForm(SCHEMA);

const names = (query: string) =>
  searchFields(FORM, query).map((group) => [
    group.section.name,
    group.fields.map((field) => field.name)
  ]);

const mount = (query: string, onSubmit = vi.fn(() => Promise.resolve())) => {
  const utils = render(
    <SchemaForm schema={SCHEMA} values={VALUES()} onSubmit={onSubmit}>
      <SchemaMatches query={query} />
      <SchemaSubmit />
    </SchemaForm>
  );
  return { onSubmit, ...utils };
};

describe('searchFields', () => {
  it('finds nothing for an empty or blank query', () => {
    expect(searchFields(FORM, '')).toEqual([]);
    expect(searchFields(FORM, '   ')).toEqual([]);
  });

  it('matches a title across every section, in declaration order', () => {
    // `Уведомления` is a leading title, `Потолок уведомлений` a title two
    // levels into another section: the search is over the whole page.
    expect(names('уведомл')).toEqual([
      [LEADING, ['notify', 'email']],
      ['calc', ['limits']]
    ]);
  });

  it('matches a description as well as a title', () => {
    expect(names('считать')).toEqual([['calc', ['currency']]]);
  });

  it('ignores case and surrounding space', () => {
    expect(names('  ВАЛЮТА  ')).toEqual(names('валюта'));
    expect(names('валюта')).toEqual([['calc', ['currency']]]);
  });

  it('returns a group whole when one of its children matches', () => {
    const [group] = searchFields(FORM, 'потолок');

    expect(group.section.name).toBe('calc');
    expect(group.fields[0].kind).toBe('group');
    // The fieldset is one thing the application grouped: half of it on screen
    // is a different form, not a narrower one.
    expect(group.fields[0].fields!.map((field) => field.name)).toEqual([
      'retries',
      'ceiling'
    ]);
  });

  it('gives the leading section no title of its own', () => {
    const [group] = searchFields(FORM, 'почта');

    expect(group.section).toEqual({ name: LEADING, title: '' });
    expect(searchFields(FORM, 'период')[0].section).toEqual({
      name: 'watch',
      title: 'Слежение'
    });
  });

  it('answers a query nothing carries with no groups at all', () => {
    expect(searchFields(FORM, 'zzz')).toEqual([]);
  });
});

describe('SchemaMatches', () => {
  it('groups the matches under the section they came from', () => {
    mount('уведомл');

    // The leading group is labelled by the translation, not by schema text:
    // msgspec never named that section.
    expect(screen.getByText('account.general')).toBeInTheDocument();
    expect(screen.getByText('Расчёт')).toBeInTheDocument();
    expect(screen.getByLabelText('Уведомления')).toBeInTheDocument();
    expect(screen.getByLabelText('Потолок уведомлений')).toBeInTheDocument();
    // A section with no match is not drawn as an empty heading.
    expect(screen.queryByText('Слежение')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Валюта')).not.toBeInTheDocument();
  });

  it('says so when nothing matches', () => {
    mount('zzz');

    expect(screen.getByText('account.search.empty')).toBeInTheDocument();
    expect(screen.queryByText('account.general')).not.toBeInTheDocument();
  });

  it('binds a match to the same form, so editing it and saving works', async () => {
    const { onSubmit } = mount('потолок');

    fireEvent.change(screen.getByLabelText('Потолок уведомлений'), {
      target: { value: '9' }
    });
    fireEvent.click(screen.getByText('account.actions.save'));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    // The whole account goes back, the edit included and at its real address:
    // the matches are the fields themselves, not a second form over a copy.
    expect(onSubmit.mock.calls[0][0]).toEqual({
      ...VALUES(),
      calc: { margin: 20, currency: 'USD', limits: { retries: 3, ceiling: 9 } }
    });
  });
});
