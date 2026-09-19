import { Controller, FieldValues, useFormContext } from 'react-hook-form';

import { Markdown } from '@/components/Markdown';
import { Translator } from '@/components/i18n';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';

import Cards from './Cards';
import EnumSelect, { optionLabel } from './EnumSelect';
import TagsInput from './TagsInput';
import { useSchemaForm } from './index';
import type { ResolvedField } from './resolve';

interface Props {
  field: ResolvedField;
}

const asList = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];

const Description = ({ text }: { text?: string }) =>
  text ? <p className="text-sm text-muted-foreground">{text}</p> : null;

/**
 * One resolved field, drawn.
 *
 * Every `disabled` below sits on the DOM element, never on the `Controller`:
 * react-hook-form's own `disabled` prop drops the field from the submitted
 * object, which would quietly delete every read-only value the server sent —
 * `plan`, a link, anything the page only displays.
 */
const Field = ({ field }: Props) => {
  const { control } = useFormContext<FieldValues>();
  const { readonly } = useSchemaForm();
  const name = field.path.join('.');
  const off = readonly || field.readOnly;

  // Outside the Controller below: a card list owns one Controller per switch
  // it draws, and a Controller around all of them would register the array
  // itself as a field.
  if (field.kind === 'cards') {
    return <Cards field={field} />;
  }

  if (field.kind === 'group') {
    return (
      <fieldset className="flex flex-col gap-4 rounded-lg border p-4">
        <legend className="px-1 text-sm font-medium">{field.title}</legend>
        <Description text={field.description} />
        {(field.fields ?? []).map((child) => (
          <Field key={child.path.join('.')} field={child} />
        ))}
      </fieldset>
    );
  }

  return (
    <Controller
      control={control}
      name={name}
      rules={
        // An empty number box on a field that cannot be null has no value to
        // send; say so here rather than let the server answer with a path.
        field.kind === 'number' && !field.nullable && !off
          ? {
              validate: (value) =>
                value === '' ||
                value === null ||
                value === undefined ||
                Number.isNaN(value)
                  ? 'Required'
                  : true
            }
          : undefined
      }
      render={({ field: rhf, fieldState }) => {
        const error = fieldState.error?.message ? (
          <p className="text-sm text-destructive">{fieldState.error.message}</p>
        ) : null;

        switch (field.kind) {
          case 'markdown':
            return (
              <div className="flex flex-col gap-1">
                <Markdown>{String(rhf.value ?? '')}</Markdown>
              </div>
            );

          case 'link': {
            const href = String(rhf.value ?? '');
            if (!href) return <div />;
            return (
              <div className="flex flex-col gap-2">
                <div>
                  <Button asChild variant="outline">
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      id={name}
                    >
                      {field.title}
                    </a>
                  </Button>
                </div>
                <Description text={field.description} />
              </div>
            );
          }

          case 'boolean':
            return (
              <div className="flex items-center justify-between gap-4">
                <div className="flex flex-col gap-1">
                  <Label htmlFor={name}>{field.title}</Label>
                  <Description text={field.description} />
                </div>
                <Switch
                  id={name}
                  checked={rhf.value === true}
                  disabled={off}
                  onCheckedChange={(checked) => rhf.onChange(checked)}
                />
              </div>
            );

          case 'enum':
            return (
              <div className="flex flex-col gap-2">
                <Label htmlFor={name}>{field.title}</Label>
                {field.widget === 'radio' ? (
                  <div role="radiogroup" className="flex flex-col gap-2">
                    {(field.enumValues ?? []).map((candidate) => (
                      <Label
                        key={String(candidate)}
                        className="flex items-center gap-2 font-normal"
                      >
                        <input
                          type="radio"
                          name={name}
                          value={String(candidate)}
                          checked={rhf.value === candidate}
                          disabled={off}
                          onChange={() => rhf.onChange(candidate)}
                        />
                        {optionLabel(candidate, field.enumLabels)}
                      </Label>
                    ))}
                  </div>
                ) : (
                  <EnumSelect
                    id={name}
                    values={field.enumValues ?? []}
                    labels={field.enumLabels}
                    value={rhf.value}
                    disabled={off}
                    onChange={rhf.onChange}
                  />
                )}
                <Description text={field.description} />
                {error}
              </div>
            );

          case 'number': {
            const step = field.step === 'any' ? 'any' : field.step;
            return (
              <div className="flex flex-col gap-2">
                <Label htmlFor={name}>{field.title}</Label>
                {field.widget === 'slider' ? (
                  <div className="flex items-center gap-3">
                    <input
                      id={name}
                      type="range"
                      className="w-full"
                      min={field.min}
                      max={field.max}
                      step={step}
                      value={
                        rhf.value === null ||
                        rhf.value === undefined ||
                        rhf.value === ''
                          ? field.min
                          : Number(rhf.value)
                      }
                      disabled={off}
                      onChange={(event) =>
                        rhf.onChange(Number(event.target.value))
                      }
                    />
                    <span className="w-14 shrink-0 text-right text-sm tabular-nums">
                      {rhf.value === null || rhf.value === undefined
                        ? ''
                        : String(rhf.value)}
                    </span>
                  </div>
                ) : (
                  <Input
                    id={name}
                    type="number"
                    min={field.min}
                    max={field.max}
                    step={step}
                    disabled={off}
                    value={
                      rhf.value === null || rhf.value === undefined
                        ? ''
                        : String(rhf.value)
                    }
                    onChange={(event) => {
                      const raw = event.target.value;
                      if (raw === '') {
                        rhf.onChange(field.nullable ? null : '');
                        return;
                      }
                      rhf.onChange(Number(raw));
                    }}
                  />
                )}
                <Description text={field.description} />
                {error}
              </div>
            );
          }

          case 'string': {
            const text =
              rhf.value === null || rhf.value === undefined
                ? ''
                : String(rhf.value);
            // An emptied nullable string is `null`, not `""` — the two are
            // different values to the Struct, and only one of them is "unset".
            const write = (raw: string) => {
              if (raw === '') {
                rhf.onChange(field.nullable ? null : '');
                return;
              }
              // `datetime-local` drops the seconds when they are zero, and
              // msgspec decodes `datetime` as RFC 3339, which requires them:
              // `msgspec.convert('2026-09-19T12:00', datetime)` raises
              // "Invalid RFC3339 encoded datetime" (verified on 0.21.1).
              rhf.onChange(
                field.format === 'date-time' &&
                  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(raw)
                  ? `${raw}:00`
                  : raw
              );
            };
            const type =
              field.format === 'date'
                ? 'date'
                : field.format === 'date-time'
                  ? 'datetime-local'
                  : field.widget === 'password'
                    ? 'password'
                    : 'text';
            return (
              <div className="flex flex-col gap-2">
                <Label htmlFor={name}>{field.title}</Label>
                {field.widget === 'textarea' ? (
                  <Textarea
                    id={name}
                    value={text}
                    disabled={off}
                    onChange={(event) => write(event.target.value)}
                  />
                ) : (
                  <Input
                    id={name}
                    type={type}
                    value={text}
                    disabled={off}
                    onChange={(event) => write(event.target.value)}
                  />
                )}
                <Description text={field.description} />
                {error}
              </div>
            );
          }

          case 'tags':
            return (
              <div className="flex flex-col gap-2">
                <Label htmlFor={name}>{field.title}</Label>
                <TagsInput
                  id={name}
                  value={asList(rhf.value).map(String)}
                  disabled={off}
                  onChange={rhf.onChange}
                />
                <Description text={field.description} />
              </div>
            );

          case 'multiselect': {
            const chosen = asList(rhf.value);
            return (
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium leading-none">
                  {field.title}
                </span>
                <div className="flex flex-col gap-2">
                  {(field.enumValues ?? []).map((candidate) => {
                    const id = `${name}.${String(candidate)}`;
                    return (
                      <div
                        key={String(candidate)}
                        className="flex items-center gap-2"
                      >
                        <Checkbox
                          id={id}
                          checked={chosen.includes(candidate)}
                          disabled={off}
                          onCheckedChange={(checked) =>
                            rhf.onChange(
                              checked === true
                                ? [
                                    ...(field.enumValues ?? []).filter(
                                      (value) =>
                                        value === candidate ||
                                        chosen.includes(value)
                                    )
                                  ]
                                : chosen.filter((value) => value !== candidate)
                            )
                          }
                        />
                        <Label htmlFor={id} className="font-normal">
                          {optionLabel(candidate, field.enumLabels)}
                        </Label>
                      </div>
                    );
                  })}
                </div>
                <Description text={field.description} />
              </div>
            );
          }

          default:
            return (
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium leading-none">
                  {field.title}
                </span>
                <p className="text-sm text-muted-foreground">
                  <Translator path="account.unsupported" />
                </p>
                <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs">
                  {JSON.stringify(rhf.value ?? null, null, 2)}
                </pre>
              </div>
            );
        }
      }}
    />
  );
};

export default Field;
