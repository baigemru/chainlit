/**
 * The account page's wire shape.
 *
 * The application declares one `msgspec.Struct`; the engine publishes its JSON
 * Schema and validates a save against the same Struct. Nothing here is a
 * mirror of a Python widget class — `IJsonSchema` is plain JSON Schema as
 * `msgspec.json.schema` emits it, plus the `x-` keys this fork reads out of
 * `Meta(extra_json_schema=...)`. A control the client does not recognise is
 * rendered as read-only JSON rather than dropped: the form has to keep
 * submitting a field it cannot draw.
 */
export interface IJsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  $ref?: string;
  $defs?: Record<string, IJsonSchema>;
  anyOf?: IJsonSchema[];
  properties?: Record<string, IJsonSchema>;
  required?: string[];
  items?: IJsonSchema;
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  format?: string;
  readOnly?: boolean;
  additionalProperties?: boolean | IJsonSchema;
  'x-widget'?:
    | 'slider'
    | 'textarea'
    | 'password'
    | 'radio'
    | 'markdown'
    | 'link'
    | 'cards'
    | 'image'
    | 'title'
    | 'badge';
  'x-actions'?: IAccountAction[];
  /**
   * On a `cards` array: the element field holding the names of the
   * `x-actions` that card offers. Absent, every card offers them all.
   */
  'x-actions-field'?: string;
  /** On an `x-widget: "badge"` field: the chip's tone per value; `muted` otherwise. */
  'x-tones'?: Record<string, IAccountTone>;
  /**
   * On an `x-widget: "markdown"` field: how loud it reads. `lead` for the one
   * line a card is about (a price), `muted` for a status line under it, `boxed`
   * for a block of fine print set apart from the rest; absent, body text.
   */
  'x-appearance'?: 'lead' | 'muted' | 'boxed';
  /** A lucide name, on a nested Struct: the icon of its row in the section menu. */
  'x-icon'?: string;
  'x-enum-labels'?: Record<string, string>;
  [key: string]: unknown;
}

/**
 * A button the application put on a `cards` array or on a tab, run by
 * `POST /project/account/actions/{name}`. `icon` is a lucide name, the same
 * vocabulary `cl.Action` uses.
 */
export interface IAccountAction {
  name: string;
  /** May carry `{field}` placeholders, filled from the card the button is on. */
  label: string;
  icon?: string | null;
  /** How loudly the button asks; absent is the outlined button. */
  variant?: 'primary' | 'ghost' | null;
}

export type IAccountTone = 'success' | 'warning' | 'danger' | 'muted';

export interface IAccountPage {
  schema: IJsonSchema;
  values: Record<string, unknown>;
  readonly: boolean;
  message?: string | null;
}

/**
 * The body of `PUT /project/account`: the form, and the page it was filled
 * from.
 *
 * Both, because the engine stores the difference between them rather than the
 * document: the account row has a second writer — whatever the application
 * puts there from a background arrival — and a save that carried only `values`
 * could not be told apart from one that meant to revert it. A save with no
 * `base` is refused with 428.
 */
export interface IAccountSave {
  values: Record<string, unknown>;
  base: Record<string, unknown>;
}

/** What the action route answers; the page does one of four things with it. */
export type IAccountActionOutcome =
  | { t: 'toast'; message: string }
  | { t: 'page'; page: IAccountPage; message?: string | null }
  | {
      t: 'open_thread';
      /** `null` when there was nothing to hand over: a plain profile switch. */
      thread_id: string | null;
      chat_profile: string;
      has_transit_message: boolean;
    }
  | {
      t: 'open_url';
      /** `http` or `https` -- the route refuses anything else. */
      url: string;
      message?: string | null;
    };

export interface IAccountActionResponse {
  outcome: IAccountActionOutcome;
}

export interface IAccountActionCall {
  /** Dotted address of the element, list index included; a tab action sends the field name. */
  path: string;
  /** The card exactly as the form holds it, unsaved edits included; `null` for a tab action. */
  item: unknown | null;
}
