import { describe, expect, it } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import { resolveForm } from '@/components/SchemaForm/resolve';

/**
 * The fixture is `msgspec.json.schema` output as generated on 19.09.2026 by
 * msgspec 0.21.1 — kept verbatim, not simplified. Every assertion below is a
 * claim about what that generator emits; a tidied-up fixture would only pin
 * what we wish it emitted.
 */
const ACCOUNT: IJsonSchema = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        calc: { title: 'Расчёт', $ref: '#/$defs/Calc' },
        notify: { title: 'Уведомления', type: 'boolean', default: true },
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
          items: { enum: ['a', 'b'] },
          default: []
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

/** One flat Struct, for the cases the fixture above does not carry. */
const wrap = (properties: Record<string, IJsonSchema>): IJsonSchema => ({
  $ref: '#/$defs/Root',
  $defs: {
    Root: { title: 'Root', type: 'object', properties, required: [] }
  }
});

describe('resolveForm', () => {
  it('splits the object fields into tabs and keeps the rest leading', () => {
    const { sections, tabs } = resolveForm(ACCOUNT);

    expect(sections.map((field) => field.name)).toEqual([
      'notify',
      'tags',
      'since',
      'plan',
      'kinds'
    ]);
    expect(tabs.map((tab) => tab.name)).toEqual(['calc']);
  });

  it('takes the tab title from the property and the description from the def', () => {
    const [calc] = resolveForm(ACCOUNT).tabs;

    expect(calc.title).toBe('Расчёт');
    expect(calc.description).toBe('Параметры расчёта');
    expect(calc.fields.map((field) => field.name)).toEqual([
      'margin',
      'currency',
      'note'
    ]);
    expect(calc.fields.map((field) => field.path)).toEqual([
      ['calc', 'margin'],
      ['calc', 'currency'],
      ['calc', 'note']
    ]);
  });

  it('gives each property its kind', () => {
    const { sections, tabs } = resolveForm(ACCOUNT);
    const byName = Object.fromEntries(
      [...sections, ...tabs[0].fields].map((field) => [field.name, field])
    );

    expect(byName.notify.kind).toBe('boolean');
    expect(byName.tags.kind).toBe('tags');
    expect(byName.since.kind).toBe('string');
    expect(byName.since.format).toBe('date');
    expect(byName.plan.kind).toBe('string');
    expect(byName.plan.readOnly).toBe(true);
    expect(byName.kinds.kind).toBe('multiselect');
    expect(byName.kinds.enumValues).toEqual(['a', 'b']);
    expect(byName.margin.kind).toBe('number');
    expect(byName.currency.kind).toBe('enum');
    expect(byName.note.kind).toBe('string');
  });

  it('keeps the slider only when the schema carries both bounds', () => {
    const [calc] = resolveForm(ACCOUNT).tabs;
    const margin = calc.fields[0];

    expect(margin.widget).toBe('slider');
    expect(margin.min).toBe(0);
    expect(margin.max).toBe(100);
    expect(margin.step).toBe('any');

    const unbounded = resolveForm(
      wrap({
        margin: {
          title: 'Маржа, %',
          'x-widget': 'slider',
          type: 'number',
          default: 20
        }
      })
    ).sections[0];

    expect(unbounded.kind).toBe('number');
    expect(unbounded.widget).toBeUndefined();
  });

  it('unwraps `X | None` and keeps the keys that stayed outside the anyOf', () => {
    const { sections, tabs } = resolveForm(ACCOUNT);
    const since = sections.find((field) => field.name === 'since')!;
    const note = tabs[0].fields.find((field) => field.name === 'note')!;

    expect(since.nullable).toBe(true);
    expect(since.title).toBe('С даты');
    expect(since.format).toBe('date');

    expect(note.nullable).toBe(true);
    expect(note.title).toBe('Заметка');
    expect(note.widget).toBe('textarea');

    expect(sections.find((field) => field.name === 'plan')!.nullable).toBe(
      false
    );
  });

  it('merges an Enum declared as its own $def', () => {
    const colour = resolveForm({
      $ref: '#/$defs/Root',
      $defs: {
        Root: {
          title: 'Root',
          type: 'object',
          properties: {
            c: { title: 'Цвет', $ref: '#/$defs/Color', default: 'blue' }
          },
          required: []
        },
        Color: { title: 'Color', enum: ['blue', 'green', 'red'] }
      }
    }).sections[0];

    // The property's title wins over the def's — the def is named after the
    // Python class, which is never what the page should read.
    expect(colour.title).toBe('Цвет');
    expect(colour.kind).toBe('enum');
    expect(colour.enumValues).toEqual(['blue', 'green', 'red']);
  });

  it('passes x-enum-labels through for both enum and multiselect', () => {
    const { sections } = resolveForm(
      wrap({
        currency: {
          title: 'Валюта',
          enum: ['CNY', 'USD'],
          'x-enum-labels': { CNY: 'Юань', USD: 'Доллар' },
          default: 'USD'
        },
        kinds: {
          title: 'Виды',
          type: 'array',
          items: { enum: ['a', 'b'], 'x-enum-labels': { a: 'Первый' } },
          default: []
        }
      })
    );

    expect(sections[0].enumLabels).toEqual({ CNY: 'Юань', USD: 'Доллар' });
    expect(sections[1].enumLabels).toEqual({ a: 'Первый' });
  });

  it('reads x-widget radio and x-widget markdown', () => {
    const { sections } = resolveForm(
      wrap({
        currency: {
          title: 'Валюта',
          enum: ['CNY', 'USD'],
          'x-widget': 'radio',
          default: 'USD'
        },
        blurb: {
          title: 'О тарифе',
          type: 'string',
          readOnly: true,
          'x-widget': 'markdown',
          default: '# hi'
        }
      })
    );

    expect(sections[0].widget).toBe('radio');
    expect(sections[1].kind).toBe('markdown');
  });

  it('resolves x-widget link before the generic string rule', () => {
    const portal = resolveForm(
      wrap({
        portal: {
          title: 'Платёжный кабинет',
          readOnly: true,
          'x-widget': 'link',
          type: 'string',
          default: '/billing/portal'
        }
      })
    ).sections[0];

    expect(portal.kind).toBe('link');
    expect(portal.title).toBe('Платёжный кабинет');
    expect(portal.readOnly).toBe(true);
  });

  it('makes a nested object inside a tab a group and refuses the level below', () => {
    const { tabs } = resolveForm({
      $ref: '#/$defs/Root',
      $defs: {
        Root: {
          title: 'Root',
          type: 'object',
          properties: { a: { title: 'A', $ref: '#/$defs/A' } },
          required: []
        },
        A: {
          title: 'A',
          type: 'object',
          properties: { b: { title: 'B', $ref: '#/$defs/B' } },
          required: []
        },
        B: {
          title: 'B',
          type: 'object',
          properties: { c: { title: 'C', $ref: '#/$defs/C' } },
          required: []
        },
        C: {
          title: 'C',
          type: 'object',
          properties: { d: { type: 'string', default: '' } },
          required: []
        }
      }
    });

    const group = tabs[0].fields[0];
    expect(group.kind).toBe('group');
    expect(group.fields!.map((field) => field.name)).toEqual(['c']);
    expect(group.fields![0].kind).toBe('unsupported');
    expect(group.fields![0].path).toEqual(['a', 'b', 'c']);
  });

  it('answers a schema it does not understand with `unsupported`, not a throw', () => {
    const { sections } = resolveForm(
      wrap({
        weird: {
          title: 'Странное',
          allOf: [{ type: 'string' }, { minLength: 2 }],
          default: ''
        },
        union: {
          title: 'Союз',
          anyOf: [{ type: 'string' }, { type: 'integer' }],
          default: ''
        },
        dangling: { title: 'Потерянное', $ref: '#/$defs/Missing' },
        mapping: {
          title: 'Словарь',
          type: 'object',
          additionalProperties: { type: 'string' },
          default: {}
        }
      })
    );

    expect(sections.map((field) => field.kind)).toEqual([
      'unsupported',
      'unsupported',
      'unsupported',
      'unsupported'
    ]);
    // The title survives even when nothing else does: the page still has to
    // say which field it is refusing to draw.
    expect(sections[0].title).toBe('Странное');
  });
});
