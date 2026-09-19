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
    | 'link';
  'x-enum-labels'?: Record<string, string>;
  [key: string]: unknown;
}

export interface IAccountPage {
  schema: IJsonSchema;
  values: Record<string, unknown>;
  readonly: boolean;
  message?: string | null;
}
