import { useEffect, useState } from 'react';
import { useWatch } from 'react-hook-form';

import type { IAccountAction, IAccountTone } from '@chainlit/react-client';

import Icon from '@/components/Icon';
import { Translator } from '@/components/i18n';
import { Badge } from '@/components/ui/badge';
import { Button, type ButtonProps } from '@/components/ui/button';

import { optionLabel } from './EnumSelect';
import Field from './Field';
import { useSchemaForm } from './index';
import { type ResolvedField, isCardControl } from './resolve';

/**
 * A `list[Struct]` the application marked `x-widget: cards`.
 *
 * A card is a read-out of one element plus the two controls the contract
 * allows on it — the boolean switch, and a string the application marked
 * `x-widget: "input"` (a price the user names when the source has none).
 * Everything else is displayed, not edited: the list is the application's,
 * the page never adds or removes an element, and an input over a value the
 * user cannot commit is a promise the route does not keep. The opt-in is
 * per leaf and explicit because a stored read-out (a brand, an article) is
 * a string too, and drawing every string as a box would promise edits the
 * application never reads.
 *
 * Only the controls register with react-hook-form. The rest of the element
 * -- the `x-widget: "hidden"` leaves, which are not drawn at all, included --
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

/**
 * A weight, not a colour, as on a message's actions: the application says
 * how loudly a button asks to be pressed and the theme decides the look.
 * Anything unrecognised is the outlined button every action had before the
 * key existed -- a switch rather than a lookup, so a `variant` of
 * `"constructor"` cannot reach the object prototype.
 */
const buttonVariant = (
  variant: IAccountAction['variant']
): ButtonProps['variant'] => {
  switch (variant) {
    case 'primary':
      return 'default';
    case 'ghost':
      return 'ghost';
    default:
      return 'outline';
  }
};

// The separators a label puts between its words and a value: `·`, `•`,
// `|`, dashes, `:` and `,`.
const SEPARATOR = '[·•|—–:,-]';
const PLACEHOLDER = new RegExp(
  `(\\s*${SEPARATOR}\\s*)?\\{([A-Za-z_][A-Za-z0-9_]*)\\}`,
  'g'
);
const EDGES = new RegExp(`^(?:\\s|${SEPARATOR})+|(?:\\s|${SEPARATOR})+$`, 'g');

/** A scalar as label text; anything else has no words to put in a button. */
const placeholderText = (value: unknown): string =>
  typeof value === 'string'
    ? value.trim()
    : typeof value === 'number' || typeof value === 'boolean'
      ? String(value)
      : '';

/**
 * An action's `label` with its `{field}` placeholders filled from the card.
 *
 * A placeholder whose field is missing, empty or not a scalar is removed
 * together with the separator in front of it, so `"Оплатить · {due_label}"`
 * on a card with no amount reads `"Оплатить"`, not `"Оплатить · "`; a
 * separator left dangling at either end -- `"{amount} · Оплатить"` -- is
 * trimmed off with it. A tab action has no card, so every placeholder in its
 * label is empty. The result is a React text child, never markup: a value
 * carrying `<b>` is drawn as the four characters it is.
 */
export const fillLabel = (label: string, item: unknown): string => {
  const record =
    item !== null && typeof item === 'object'
      ? (item as Record<string, unknown>)
      : {};
  let emptied = false;
  const filled = label.replace(
    PLACEHOLDER,
    (_match, separator: string | undefined, key: string) => {
      const text = Object.prototype.hasOwnProperty.call(record, key)
        ? placeholderText(record[key])
        : '';
      if (!text) emptied = true;
      return text ? `${separator ?? ''}${text}` : '';
    }
  );
  return emptied ? filled.replace(EDGES, '').replace(/\s{2,}/g, ' ') : filled;
};

interface ActionButtonsProps {
  actions?: IAccountAction[];
  /** Dotted address the route resolves the element type from. */
  path: string;
  /** The card as the form holds it now; `null` for a tab action. */
  item: unknown;
  onAction?: ActionHandler;
}

/**
 * The `x-actions` buttons, on a card or in a tab header: each label filled
 * by `fillLabel`, each weight picked by `buttonVariant`. Which of them a
 * card offers is decided by its caller, `Cards`, out of `x-actions-field`.
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
            variant={buttonVariant(action.variant)}
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
              fillLabel(action.label, item)
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

/**
 * The status chip's colours, written out whole so Tailwind finds every class
 * in this file; a class assembled from the tone name would never be built.
 */
const TONE_CLASS: Record<IAccountTone, string> = {
  success: 'border-transparent bg-success/15 text-success',
  warning: 'border-transparent bg-warning/15 text-warning',
  danger: 'border-transparent bg-destructive/15 text-destructive',
  muted: 'border-transparent bg-muted text-muted-foreground'
};

const isBadge = (field: ResolvedField): boolean =>
  (field.kind === 'string' || field.kind === 'enum') &&
  field.widget === 'badge';

/**
 * `x-widget: "badge"`: a value drawn as a pill in the card's header -- a
 * subscription's "Активна", "Ждёт оплаты". Its words are `x-enum-labels`
 * when they name the value, the value otherwise; its tone is `x-tones`,
 * `muted` for anything that does not name one. An empty value draws
 * nothing -- a pill with no words is a coloured dot nobody can read -- and
 * `Cards` drops such a field before it gets here, so the header row it
 * would have sat in is not drawn empty either.
 */
const StatusChip = ({
  field,
  value
}: {
  field: ResolvedField;
  value: unknown;
}) => {
  const key = asText(value);
  const text = optionLabel(value, field.enumLabels);
  const tone: IAccountTone =
    field.tones && Object.prototype.hasOwnProperty.call(field.tones, key)
      ? field.tones[key]
      : 'muted';
  return (
    <Badge
      variant="outline"
      data-testid="account-card-badge"
      data-tone={tone}
      className={`shrink-0 ${TONE_CLASS[tone]}`}
    >
      {text}
    </Badge>
  );
};

/**
 * The actions one card offers. Without `x-actions-field`, all of them. With
 * it, the ones the card's own list names, in `x-actions` order -- the order
 * the application declared the buttons in, not whichever order its state
 * machine happened to list them. A card whose field is not a list at all
 * offers none: it opted into saying what is valid in its state, and a
 * button its state does not admit is worse than a missing one.
 */
const offeredActions = (
  actions: IAccountAction[] | undefined,
  actionsField: string | undefined,
  record: Record<string, unknown>
): IAccountAction[] | undefined => {
  if (!actionsField) return actions;
  const names = record[actionsField];
  if (!Array.isArray(names)) return undefined;
  return actions?.filter((action) => names.includes(action.name));
};

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
  // `hidden` is the application's bookkeeping -- a card's id, a run, a
  // "seen" -- and a card that printed it would show the user a number they
  // would take for something that matters. Its value rides in the element.
  const badges = itemFields.filter(isBadge);
  const rest = itemFields.filter(
    (child) =>
      child !== image &&
      child !== title &&
      !isBadge(child) &&
      child.kind !== 'hidden' &&
      !isNestedObject(child)
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
        // Only the chips with something to say, so an empty status does not
        // leave an empty header row behind.
        const shown = badges.filter((child) => asText(record[child.name]));

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
              {heading || shown.length > 0 ? (
                // The title and the status on one line, the chip after the
                // title or, on a card without one, on the line by itself.
                <div className="flex flex-wrap items-start justify-between gap-2">
                  {heading ? (
                    <span className="font-medium leading-tight">{heading}</span>
                  ) : null}
                  {shown.map((child) => (
                    <StatusChip
                      key={child.name}
                      field={child}
                      value={record[child.name]}
                    />
                  ))}
                </div>
              ) : null}
              {rest.map((child) => {
                // The kinds a card shares with the rest of the form are drawn
                // by the form, not copied here: one implementation of a
                // switch, of a text box, of a link and of a Markdown block.
                if (
                  child.kind === 'markdown' ||
                  child.kind === 'link' ||
                  isCardControl(child)
                ) {
                  return <Field key={child.name} field={bind(child)} />;
                }
                // Anything with nothing to show goes undrawn; its value stays
                // in the form and goes back on the next save regardless.
                const out = readOut(child, record[child.name]);
                return out ? <div key={child.name}>{out}</div> : null;
              })}
              <ActionButtons
                actions={offeredActions(
                  field.actions,
                  field.actionsField,
                  record
                )}
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
