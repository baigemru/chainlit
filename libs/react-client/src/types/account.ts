/**
 * The account page's wire shape.
 *
 * The application declares one `msgspec.Struct`; the engine publishes its JSON
 * Schema and validates a save against the same Struct. Nothing here is a
 * mirror of a Python widget class — `IJsonSchema` is plain JSON Schema as
 * `msgspec.json.schema` emits it, plus the two `x-` keys this fork reads out of
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
    | 'title';
  'x-actions'?: IAccountAction[];
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
  label: string;
  icon?: string | null;
}

export interface IAccountPage {
  schema: IJsonSchema;
  values: Record<string, unknown>;
  readonly: boolean;
  message?: string | null;
}

/** What the action route answers; the page does one of three things with it. */
export type IAccountActionOutcome =
  | { t: 'toast'; message: string }
  | { t: 'page'; page: IAccountPage; message?: string | null }
  | {
      t: 'open_thread';
      /** `null` when there was nothing to hand over: a plain profile switch. */
      thread_id: string | null;
      chat_profile: string;
      has_transit_message: boolean;
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
