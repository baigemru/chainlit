import { useEffect, useMemo, useState } from 'react';
import { FieldValues, useForm } from 'react-hook-form';

import type { IJsonSchema } from '@chainlit/react-client';

import { Translator } from '@/components/i18n';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { ActionButtons } from './Cards';
import Field from './Field';
import { ResolvedField, resolveForm } from './resolve';

export { resolveForm } from './resolve';
export type {
  FieldKind,
  FieldWidget,
  ResolvedField,
  ResolvedForm,
  ResolvedTab
} from './resolve';

export interface SchemaFormProps {
  schema: IJsonSchema;
  values: Record<string, unknown>;
  readonly?: boolean;
  /** Resolves when the server accepted; rejects (with the server's detail) otherwise. */
  onSubmit: (values: Record<string, unknown>) => Promise<void> | void;
  /** Controlled tab. A name no tab carries shows the first one. */
  activeTab?: string;
  onTabChange?: (name: string) => void;
  /** A button from `x-actions` was pressed; `item` is the card's current form value, or null for a tab action. */
  onAction?: (
    name: string,
    path: string,
    item: unknown
  ) => Promise<void> | void;
}

/**
 * The account page's form, drawn from the JSON Schema the engine publishes.
 *
 * It renders what `msgspec.json.schema` produced and nothing else: there is no
 * widget registry, no per-field declaration on the Python side and no second
 * description of the same shape to keep in step. A field whose schema this
 * client does not recognise is shown as read-only JSON and submitted back
 * unchanged — the form's job is to never be the reason a value is lost.
 */
const SchemaForm = ({
  schema,
  values,
  readonly,
  onSubmit,
  activeTab,
  onTabChange,
  onAction
}: SchemaFormProps) => {
  const { sections, tabs } = useMemo(() => resolveForm(schema), [schema]);
  const [ownTab, setOwnTab] = useState<string | undefined>(undefined);

  // `?tab=` names a tab the application may have renamed or dropped since the
  // link was shared; the page opens on the first tab rather than on nothing.
  const wanted = activeTab ?? ownTab;
  const current = tabs.some((tab) => tab.name === wanted)
    ? (wanted as string)
    : (tabs[0]?.name ?? '');

  const {
    control,
    handleSubmit,
    reset,
    formState: { isSubmitting }
  } = useForm<FieldValues>({ defaultValues: values });

  // The page hands back what the server stored after a save, so the form has
  // to adopt it: `reset(values)` moves `defaultValues` too, which is what
  // makes the Reset button below mean "the last accepted values".
  useEffect(() => {
    reset(values);
  }, [values, reset]);

  const renderField = (field: ResolvedField) => (
    <Field
      key={field.path.join('.')}
      field={field}
      control={control}
      disabled={readonly}
      onAction={onAction}
    />
  );

  const submit = handleSubmit(async (data) => {
    try {
      await onSubmit(data as Record<string, unknown>);
    } catch {
      // The page owns the toast; here a refusal only has to give the button
      // back. Letting it escape would reach the browser as an unhandled
      // rejection, because `handleSubmit` rethrows after clearing
      // `isSubmitting`.
    }
  });

  return (
    <form
      onSubmit={submit}
      className="max-w-3xl mx-auto w-full flex flex-col gap-6"
    >
      {sections.length > 0 ? (
        <div className="flex flex-col gap-4">{sections.map(renderField)}</div>
      ) : null}

      {tabs.length > 0 ? (
        <Tabs
          value={current}
          onValueChange={(value) => {
            // A controlled caller owns the tab; keeping a second copy here is
            // how the strip and the URL end up disagreeing after a Back.
            if (activeTab === undefined) setOwnTab(value);
            onTabChange?.(value);
          }}
          className="w-full"
        >
          <TabsList className="w-full justify-start overflow-x-auto">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.name} value={tab.name}>
                {tab.title}
              </TabsTrigger>
            ))}
          </TabsList>
          {tabs.map((tab) => (
            <TabsContent
              key={tab.name}
              value={tab.name}
              // Every tab stays mounted: a Controller unmounted by a tab
              // switch takes its validation state with it, and an inactive
              // tab holding a "Required" is exactly when you need to see it.
              // `hidden` is stated here rather than left to Radix, which
              // computes it from `present` — always true under `forceMount`,
              // so every panel would be on screen at once. The attribute
              // alone is not enough: `flex` is a utility of the same
              // specificity as preflight's `[hidden]` rule and comes later,
              // so it would win and show the panel anyway. Radix still
              // stamps `data-state` under `forceMount`, and a variant on it
              // outranks the utility (seen live on 20.09: both tabs drawn).
              forceMount
              hidden={tab.name !== current}
              className="flex flex-col gap-4 pt-2 data-[state=inactive]:hidden"
            >
              {tab.description ? (
                <p className="text-sm text-muted-foreground">
                  {tab.description}
                </p>
              ) : null}
              <ActionButtons
                actions={tab.actions}
                path={tab.name}
                item={null}
                onAction={onAction}
              />
              {tab.fields.map(renderField)}
            </TabsContent>
          ))}
        </Tabs>
      ) : null}

      {readonly ? null : (
        <div className="flex gap-2">
          <Button type="submit" disabled={isSubmitting}>
            <Translator path="account.actions.save" />
          </Button>
          <Button type="button" variant="outline" onClick={() => reset()}>
            <Translator path="common.actions.reset" />
          </Button>
        </div>
      )}
    </form>
  );
};

export default SchemaForm;
