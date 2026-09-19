import type { IAccountAction, IJsonSchema } from '@chainlit/react-client';

/**
 * Flattens the JSON Schema `msgspec.json.schema` emits into the handful of
 * shapes a form can draw.
 *
 * Pure on purpose: every branch below is a claim about what msgspec 0.21.1
 * produces, and the only honest way to pin such a claim is to feed the
 * generator's real output to a function that touches nothing else.
 *
 * The one rule the rest of the form leans on: an unrecognised schema resolves
 * to `unsupported`, never to a throw. A field the client cannot draw still has
 * a stored value, and the page must keep submitting it unchanged rather than
 * refuse to open.
 */

export type FieldKind =
  | 'boolean'
  | 'enum'
  | 'number'
  | 'string'
  | 'multiselect'
  | 'tags'
  | 'markdown'
  | 'link'
  | 'cards'
  | 'group'
  | 'unsupported';

export type FieldWidget =
  | 'slider'
  | 'textarea'
  | 'password'
  | 'radio'
  | 'image'
  | 'title';

export interface ResolvedField {
  name: string;
  /** Dot-notation address for react-hook-form, split. */
  path: string[];
  title: string;
  description?: string;
  kind: FieldKind;
  /** The schema after `$ref` and nullable-union resolution. */
  schema: IJsonSchema;
  readOnly: boolean;
  nullable: boolean;
  enumValues?: unknown[];
  enumLabels?: Record<string, string>;
  min?: number;
  max?: number;
  step?: number | 'any';
  format?: string;
  /** The widget that survived its preconditions — see `sliderUsable`. */
  widget?: FieldWidget;
  /** `group` only. */
  fields?: ResolvedField[];
  /**
   * `cards` only: the element's fields, addressed **relative to one element**.
   * The card prefixes `<field>.<index>` — the index is a rendering fact, not a
   * schema one, and baking it in here would make the resolution per-card.
   */
  itemFields?: ResolvedField[];
  /** `cards` only: the buttons `x-actions` put on every card. */
  actions?: IAccountAction[];
}

export interface ResolvedTab {
  name: string;
  title: string;
  description?: string;
  fields: ResolvedField[];
  /** The buttons `x-actions` put in the tab header. */
  actions?: IAccountAction[];
}

export interface ResolvedForm {
  /**
   * The top-level fields that are not objects, in declaration order. They are
   * drawn above the tab strip as one leading section with no heading: msgspec
   * gives us no name for them, and "General" would be an invention the
   * application never wrote.
   */
  sections: ResolvedField[];
  tabs: ResolvedTab[];
}

const DEF_PREFIX = '#/$defs/';

/** A `$ref` chain deeper than this is a schema we do not understand. */
const MAX_DEPTH = 8;

interface Resolution {
  schema: IJsonSchema;
  nullable: boolean;
  unsupported: boolean;
}

const isSchema = (value: unknown): value is IJsonSchema =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * `$ref` → the definition, with the property's own keys layered back over it.
 *
 * The layering is load-bearing in both directions msgspec uses: for an `Enum`
 * class the `enum` lives in the def while `title` and `default` stay on the
 * property, and for a nullable field the `format` lives inside the `anyOf`
 * while `title` and `x-widget` stay outside it. Unwrapping without re-layering
 * silently drops whichever half the author actually wrote.
 */
const resolveSchema = (
  node: IJsonSchema,
  defs: Record<string, IJsonSchema>,
  depth = 0
): Resolution => {
  if (depth > MAX_DEPTH) {
    return { schema: node, nullable: false, unsupported: true };
  }

  if (typeof node.$ref === 'string') {
    const { $ref, ...own } = node;
    const def = $ref.startsWith(DEF_PREFIX)
      ? defs[$ref.slice(DEF_PREFIX.length)]
      : undefined;
    if (!isSchema(def)) {
      return { schema: own, nullable: false, unsupported: true };
    }
    const inner = resolveSchema(def, defs, depth + 1);
    return { ...inner, schema: { ...inner.schema, ...own } };
  }

  if (Array.isArray(node.anyOf)) {
    const { anyOf, ...own } = node;
    const branches = anyOf.filter(isSchema);
    const nulls = branches.filter((branch) => branch.type === 'null');
    const rest = branches.filter((branch) => branch.type !== 'null');
    // `X | None` and nothing else. A real union has no single control.
    if (nulls.length === 1 && rest.length === 1) {
      const inner = resolveSchema(rest[0], defs, depth + 1);
      return {
        schema: { ...inner.schema, ...own },
        nullable: true,
        unsupported: inner.unsupported
      };
    }
    return { schema: own, nullable: false, unsupported: true };
  }

  if (node.oneOf || node.allOf || Array.isArray(node.type)) {
    return { schema: node, nullable: false, unsupported: true };
  }

  return { schema: node, nullable: false, unsupported: false };
};

const widgetOf = (schema: IJsonSchema): string | undefined => {
  const widget = schema['x-widget'];
  return typeof widget === 'string' ? widget : undefined;
};

const labelsOf = (schema: IJsonSchema): Record<string, string> | undefined => {
  const labels = schema['x-enum-labels'];
  return isSchema(labels) ? (labels as Record<string, string>) : undefined;
};

/**
 * `x-actions`, entry by entry.
 *
 * An entry missing `name` or `label` is dropped rather than drawn: a button
 * with no name posts to no route, and one with no label is a blank rectangle
 * the user is invited to press.
 */
const actionsOf = (schema: IJsonSchema): IAccountAction[] | undefined => {
  const raw: unknown = schema['x-actions'];
  if (!Array.isArray(raw)) return undefined;
  const actions = (raw as unknown[]).filter(
    (entry): entry is IAccountAction =>
      isSchema(entry) &&
      typeof entry.name === 'string' &&
      typeof entry.label === 'string'
  );
  return actions.length > 0 ? actions : undefined;
};

const stepOf = (schema: IJsonSchema): number | 'any' => {
  if (typeof schema.multipleOf === 'number') return schema.multipleOf;
  return schema.type === 'integer' ? 1 : 'any';
};

const isObjectSchema = (resolution: Resolution): boolean =>
  !resolution.unsupported &&
  resolution.schema.type === 'object' &&
  isSchema(resolution.schema.properties);

const buildField = (
  name: string,
  node: IJsonSchema,
  parentPath: string[],
  defs: Record<string, IJsonSchema>,
  allowGroup: boolean
): ResolvedField => {
  const resolution = resolveSchema(node, defs);
  const schema = resolution.schema;
  const path = [...parentPath, name];

  const base = {
    name,
    path,
    title: typeof schema.title === 'string' ? schema.title : name,
    description:
      typeof schema.description === 'string' ? schema.description : undefined,
    schema,
    readOnly: schema.readOnly === true,
    nullable: resolution.nullable
  };

  const unsupported = (): ResolvedField => ({
    ...base,
    kind: 'unsupported'
  });

  if (resolution.unsupported) return unsupported();

  const widget = widgetOf(schema);

  if (widget === 'markdown') {
    return { ...base, kind: 'markdown' };
  }

  // Before the generic string rule: a link is a string whose value is an
  // address, and a disabled text box showing "/billing/portal" is not a way
  // into the billing portal.
  if (widget === 'link') {
    return { ...base, kind: 'link' };
  }

  if (schema.type === 'boolean') {
    return { ...base, kind: 'boolean' };
  }

  if (Array.isArray(schema.enum)) {
    return {
      ...base,
      kind: 'enum',
      enumValues: schema.enum,
      enumLabels: labelsOf(schema),
      widget: widget === 'radio' ? 'radio' : undefined
    };
  }

  if (schema.type === 'integer' || schema.type === 'number') {
    const min = typeof schema.minimum === 'number' ? schema.minimum : undefined;
    const max = typeof schema.maximum === 'number' ? schema.maximum : undefined;
    // A range input with no range is a slider from 0 to 100 over a field that
    // means neither; fall back to the number box rather than invent bounds.
    const sliderUsable =
      widget === 'slider' && min !== undefined && max !== undefined;
    return {
      ...base,
      kind: 'number',
      min,
      max,
      step: stepOf(schema),
      widget: sliderUsable ? 'slider' : undefined
    };
  }

  if (schema.type === 'string') {
    return {
      ...base,
      kind: 'string',
      format: typeof schema.format === 'string' ? schema.format : undefined,
      // `image` and `title` mean something only inside a card; outside one the
      // card component never sees them and Field falls back to a text box,
      // which is what a string field is.
      widget:
        widget === 'textarea' ||
        widget === 'password' ||
        widget === 'image' ||
        widget === 'title'
          ? widget
          : undefined
    };
  }

  if (schema.type === 'array') {
    if (!isSchema(schema.items)) return unsupported();
    const items = resolveSchema(schema.items, defs);
    if (items.unsupported) return unsupported();
    // An array of objects is a list of cards only when the application asked
    // for cards. Without the widget there is no layout for it, and guessing
    // one would draw a Struct the author meant to keep off the page.
    if (widget === 'cards' && isObjectSchema(items)) {
      return {
        ...base,
        kind: 'cards',
        itemFields: buildFields(items.schema, [], defs, false),
        actions: actionsOf(schema)
      };
    }
    if (Array.isArray(items.schema.enum)) {
      return {
        ...base,
        kind: 'multiselect',
        enumValues: items.schema.enum,
        enumLabels: labelsOf(items.schema) ?? labelsOf(schema)
      };
    }
    if (items.schema.type === 'string') {
      return { ...base, kind: 'tags' };
    }
    return unsupported();
  }

  if (isObjectSchema(resolution)) {
    // One level of nesting inside a tab is a fieldset; two is a schema this
    // form has no layout for, and drawing it wrong is worse than saying so.
    if (!allowGroup) return unsupported();
    return {
      ...base,
      kind: 'group',
      fields: buildFields(schema, path, defs, false)
    };
  }

  return unsupported();
};

const buildFields = (
  object: IJsonSchema,
  parentPath: string[],
  defs: Record<string, IJsonSchema>,
  allowGroup: boolean
): ResolvedField[] => {
  const properties = isSchema(object.properties) ? object.properties : {};
  return Object.entries(properties)
    .filter(([, node]) => isSchema(node))
    .map(([name, node]) =>
      buildField(name, node as IJsonSchema, parentPath, defs, allowGroup)
    );
};

export const resolveForm = (schema: IJsonSchema): ResolvedForm => {
  const defs = isSchema(schema.$defs)
    ? (schema.$defs as Record<string, IJsonSchema>)
    : {};
  const root = resolveSchema(schema, defs).schema;
  const properties = isSchema(root.properties) ? root.properties : {};

  const sections: ResolvedField[] = [];
  const tabs: ResolvedTab[] = [];

  // Declaration order is the application's layout: msgspec keeps Struct field
  // order in `properties`, and nothing here may reorder it.
  for (const [name, node] of Object.entries(properties)) {
    if (!isSchema(node)) continue;
    const resolution = resolveSchema(node, defs);
    if (isObjectSchema(resolution)) {
      tabs.push({
        name,
        title:
          typeof resolution.schema.title === 'string'
            ? resolution.schema.title
            : name,
        description:
          typeof resolution.schema.description === 'string'
            ? resolution.schema.description
            : undefined,
        fields: buildFields(resolution.schema, [name], defs, true),
        // The property's keys are layered over the `$ref`ed def, so an
        // `x-actions` written beside the `$ref` is visible here.
        actions: actionsOf(resolution.schema)
      });
      continue;
    }
    sections.push(buildField(name, node, [], defs, false));
  }

  return { sections, tabs };
};
