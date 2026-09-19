import { useEffect, useState } from 'react';
import { useWatch } from 'react-hook-form';

import type { IAccountAction } from '@chainlit/react-client';

import Icon from '@/components/Icon';
import { Translator } from '@/components/i18n';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { optionLabel } from './EnumSelect';
import Field from './Field';
import { useSchemaForm } from './index';
import type { ResolvedField } from './resolve';

/**
 * A `list[Struct]` the application marked `x-widget: cards`.
 *
 * A card is a read-out of one element plus the single control the contract
 * allows on it — the boolean switch. Everything else is displayed, not edited:
 * the list is the application's, the page never adds or removes an element,
 * and an input over a value the user cannot commit is a promise the route does
 * not keep.
 *
 * Only the switches register with react-hook-form. The rest of the element
 * still reaches `PUT /project/account` untouched, because `handleSubmit`
 * submits a clone of `_formValues` — seeded from `defaultValues` — rather than
 * a walk over the registered fields (react-hook-form 7.54.2,
 * `dist/index.esm.mjs:2278`).
 */

export type ActionHandler = (
  name: string,
  path: string,
  item: unknown
) => Promise<void> | void;

interface ActionButtonsProps {
  actions?: IAccountAction[];
  /** Dotted address the route resolves the element type from. */
  path: string;
  /** The card as the form holds it now; `null` for a tab action. */
  item: unknown;
  onAction?: ActionHandler;
}

/**
 * The `x-actions` buttons, on a card or in a tab header.
 *
 * `type="button"` is load-bearing: the default inside a `<form>` is `submit`,
 * so a card action would save the whole account on the way to the route.
 */
export const ActionButtons = ({
  actions,
  path,
  item,
  onAction
}: ActionButtonsProps) => {
  const [running, setRunning] = useState<string | null>(null);

  if (!actions?.length || !onAction) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((action) => {
        const busy = running === action.name;
        return (
          <Button
            key={action.name}
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            aria-busy={busy}
            onClick={async () => {
              setRunning(action.name);
              try {
                await onAction(action.name, path, item);
              } catch {
                // The page owns the toast. Letting a refusal escape a click
                // handler reaches the browser as an unhandled rejection, and
                // the button would never come back.
              } finally {
                setRunning(null);
              }
            }}
          >
            {action.icon ? <Icon name={action.icon} /> : null}
            {busy ? (
              <Translator path="account.actions.working" />
            ) : (
              action.label
            )}
          </Button>
        );
      })}
    </div>
  );
};

interface Props {
  field: ResolvedField;
}

const asText = (value: unknown): string =>
  value === null || value === undefined ? '' : String(value);

/** A field the resolver refused that is, specifically, a nested Struct. */
const isNestedObject = (field: ResolvedField): boolean =>
  field.kind === 'unsupported' &&
  field.schema.type === 'object' &&
  field.schema.properties !== undefined;

const Cards = ({ field }: Props) => {
  const { onAction } = useSchemaForm();
  const name = field.path.join('.');
  const itemFields = field.itemFields ?? [];
  // No `control`: `useWatch` takes it from the `FormProvider` the form put up.
  const list = useWatch({ name });
  // A stored value can be missing the key entirely — a card list that throws
  // on that would take the whole page down with it.
  const items: unknown[] = Array.isArray(list) ? list : [];

  const image = itemFields.find(
    (child) => child.kind === 'string' && child.widget === 'image'
  );
  const title = itemFields.find(
    (child) => child.kind === 'string' && child.widget === 'title'
  );
  const rest = itemFields.filter(
    (child) => child !== image && child !== title && !isNestedObject(child)
  );

  // Once per field, not once per card: the author needs the address, not one
  // line per element of a twenty-item list.
  useEffect(() => {
    for (const child of itemFields) {
      if (isNestedObject(child)) {
        console.error(
          `SchemaForm: no card layout for the nested object at \`${name}.*.${child.name}\`; its value is submitted unchanged.`
        );
      }
    }
  }, [itemFields, name]);

  if (items.length === 0) {
    return field.description ? (
      <p className="text-sm text-muted-foreground">{field.description}</p>
    ) : null;
  }

  const readOut = (child: ResolvedField, value: unknown) => {
    if (child.kind === 'tags') {
      const tags = Array.isArray(value) ? value : [];
      if (tags.length === 0) return null;
      return (
        <div className="flex flex-wrap gap-1">
          {tags.map((tag) => (
            <Badge key={String(tag)} variant="secondary">
              {String(tag)}
            </Badge>
          ))}
        </div>
      );
    }

    const text =
      child.kind === 'enum'
        ? optionLabel(value, child.enumLabels)
        : child.kind === 'boolean'
          ? // No `common.yes` / `common.no` exists and this file does not own
            // the translations; a glyph needs neither.
            value === true
            ? '✓'
            : '—'
          : asText(value);
    if (!text) return null;

    return (
      <div className="flex gap-2 text-sm">
        <span className="shrink-0 text-muted-foreground">{child.title}:</span>
        <span className="min-w-0 break-words">{text}</span>
      </div>
    );
  };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {items.map((item, index) => {
        const record = (item ?? {}) as Record<string, unknown>;
        const prefix = [...field.path, String(index)];
        const bind = (child: ResolvedField): ResolvedField => ({
          ...child,
          path: [...prefix, ...child.path]
        });
        const src = image ? asText(record[image.name]) : '';
        const heading = title ? asText(record[title.name]) : '';

        return (
          <div
            key={index}
            className="flex gap-4 rounded-lg border p-4"
            data-testid="account-card"
          >
            {src ? (
              <img
                src={src}
                alt={heading}
                className="size-24 shrink-0 rounded-md object-cover"
              />
            ) : null}
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {heading ? (
                <span className="font-medium leading-tight">{heading}</span>
              ) : null}
              {rest.map((child) => {
                // The three kinds a card shares with the rest of the form are
                // drawn by the form, not copied here: one implementation of a
                // switch, of a link and of a Markdown block.
                if (
                  child.kind === 'markdown' ||
                  child.kind === 'link' ||
                  (child.kind === 'boolean' && !child.readOnly)
                ) {
                  return <Field key={child.name} field={bind(child)} />;
                }
                // Anything with nothing to show goes undrawn; its value stays
                // in the form and goes back on the next save regardless.
                const out = readOut(child, record[child.name]);
                return out ? <div key={child.name}>{out}</div> : null;
              })}
              <ActionButtons
                actions={field.actions}
                path={prefix.join('.')}
                item={record}
                onAction={onAction}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default Cards;
