import { createContext, useContext, useEffect, useMemo, useRef } from 'react';
import {
  FieldValues,
  FormProvider,
  useForm,
  useFormContext
} from 'react-hook-form';

import type { IJsonSchema } from '@chainlit/react-client';

import { Translator } from '@/components/i18n';
import { Button } from '@/components/ui/button';

import type { ActionHandler } from './Cards';
import Field from './Field';
import {
  LEADING,
  ResolvedField,
  ResolvedForm,
  hasEditable,
  resolveForm,
  searchFields
} from './resolve';

export { LEADING, hasEditable, resolveForm, searchFields } from './resolve';
export type {
  FieldKind,
  FieldWidget,
  ResolvedField,
  ResolvedForm,
  ResolvedTab,
  SearchGroup
} from './resolve';

export interface SchemaFormProps {
  schema: IJsonSchema;
  values: Record<string, unknown>;
  readonly?: boolean;
  /**
   * Resolves when the server accepted; rejects (with the server's detail)
   * otherwise. `base` is the page these values were filled from, which the
   * save carries so the engine can store the difference rather than the
   * document.
   */
  onSubmit: (
    values: Record<string, unknown>,
    base: Record<string, unknown>
  ) => Promise<void> | void;
  /** A button from `x-actions` was pressed; `item` is the card's current form value, or null for a tab action. */
  onAction?: ActionHandler;
  /** The layout: sections, matches and the Save/Reset row, in the caller's order. */
  children: React.ReactNode;
  /** On the `<form>` element. */
  className?: string;
}

interface SchemaFormState {
  form: ResolvedForm;
  readonly: boolean;
  onAction?: ActionHandler;
  isSubmitting: boolean;
}

const SchemaFormContext = createContext<SchemaFormState | null>(null);

/**
 * The resolved schema and the form's own state, for the pieces below and for
 * the layout around them.
 *
 * Deliberately separate from react-hook-form's `FormProvider`: RHF already
 * owns the values and every field reads them through `useFormContext`, so
 * what is left here is only what the schema said — which is not RHF's
 * business and would have to be smuggled through `useForm`'s context if it
 * were one object.
 */
export const useSchemaForm = (): SchemaFormState => {
  const state = useContext(SchemaFormContext);
  if (!state) {
    throw new Error('useSchemaForm must be used inside a <SchemaForm>');
  }
  return state;
};

/**
 * The leaves of react-hook-form's `dirtyFields`, as paths -- or `null` when
 * one of them runs through a list.
 *
 * `null` is the refusal the adoption below needs: a list is addressed by
 * position, a page that arrives may have added or removed an element, and a
 * draft put back at `feed.entries.3.seen` would land on whichever card is
 * third now. Exported for the spec; nothing else needs it.
 */
export const draftLeaves = (
  dirty: unknown,
  path: string[] = []
): string[][] | null => {
  if (dirty === true) return [path];
  if (Array.isArray(dirty)) return null;
  if (typeof dirty !== 'object' || dirty === null) return [];
  const leaves: string[][] = [];
  for (const [key, child] of Object.entries(dirty)) {
    const found = draftLeaves(child, [...path, key]);
    if (found === null) return null;
    leaves.push(...found);
  }
  return leaves;
};

const renderField = (field: ResolvedField) => (
  <Field key={field.path.join('.')} field={field} />
);

/**
 * The account page's form, drawn from the JSON Schema the engine publishes.
 *
 * It renders what `msgspec.json.schema` produced and nothing else: there is no
 * widget registry, no per-field declaration on the Python side and no second
 * description of the same shape to keep in step. A field whose schema this
 * client does not recognise is shown as read-only JSON and submitted back
 * unchanged — the form's job is to never be the reason a value is lost.
 *
 * It is a provider, not a layout. Where the sections go, what the header of
 * one says and whether a search box is over them is the dialog's question,
 * and a form that also answered it would have to be re-cut every time that
 * dialog moves a box.
 */
const SchemaForm = ({
  schema,
  values,
  readonly,
  onSubmit,
  onAction,
  children,
  className
}: SchemaFormProps) => {
  const form = useMemo(() => resolveForm(schema), [schema]);

  const methods = useForm<FieldValues>({ defaultValues: values });
  const {
    handleSubmit,
    reset,
    getValues,
    setValue,
    // Read here so react-hook-form keeps both up to date: it computes the
    // dirty state only for a form that subscribed to it, and the adoption
    // below reads it from an effect, which does not subscribe.
    formState: { isSubmitting, isDirty, dirtyFields }
  } = methods;

  // What the form was last filled from, which is not always the `values`
  // prop: SWR can revalidate under an open form, and the save has to report
  // the page the user was actually editing. Sending a newer one would read as
  // "the user deleted everything that arrived since", and the engine would
  // do it.
  const base = useRef(values);

  // Set while a save is out, and consumed by the first page that arrives
  // after it: that page is the answer to the save and is adopted whole.
  // Consumed rather than cleared when the save resolves, because the
  // response lands in SWR and reaches this component on a render of its own,
  // which may come after the `await` below has already returned.
  const saving = useRef(false);

  // A new page is adopted: `reset(values)` moves `defaultValues` too, which
  // is what makes the Reset button below mean "the last accepted values".
  //
  // A page can also arrive under a draft -- the dialog asks again when the
  // user picks another section, so the application hears which one is
  // open -- and a reset would then throw away what the user typed in the
  // section they just left. So the draft's leaves are put back on top of
  // the new page, and `base` moves with it: the save is the difference
  // between the two, and that difference is exactly the draft. A draft
  // inside a list is the exception (see `draftLeaves`): the old page is
  // kept, with the draft, and the next save merges onto the store as usual.
  useEffect(() => {
    if (saving.current || !isDirty) {
      saving.current = false;
      base.current = values;
      reset(values);
      return;
    }
    const leaves = draftLeaves(dirtyFields);
    if (leaves === null) return;
    const draft = leaves.map((path) => {
      const name = path.join('.');
      return [name, getValues(name)] as const;
    });
    base.current = values;
    reset(values);
    for (const [name, value] of draft) {
      setValue(name, value, { shouldDirty: true });
    }
    // Only on a new page: the dirty state is read here, not reacted to.
  }, [values, reset]);

  const submit = handleSubmit(async (data) => {
    saving.current = true;
    try {
      await onSubmit(data as Record<string, unknown>, base.current);
    } catch {
      // No page follows a refusal, so nothing would consume the mark, and
      // the next arrival -- a section change -- would reset over the draft
      // the user is about to fix.
      saving.current = false;
      // The page owns the toast; here a refusal only has to give the button
      // back. Letting it escape would reach the browser as an unhandled
      // rejection, because `handleSubmit` rethrows after clearing
      // `isSubmitting`.
    }
  });

  const state = useMemo(
    () => ({ form, readonly: readonly === true, onAction, isSubmitting }),
    [form, readonly, onAction, isSubmitting]
  );

  return (
    <FormProvider {...methods}>
      <SchemaFormContext.Provider value={state}>
        <form onSubmit={submit} className={className}>
          {children}
        </form>
      </SchemaFormContext.Provider>
    </FormProvider>
  );
};

/**
 * One section's fields: `LEADING` for the top-level scalars, otherwise the
 * nested Struct of that name.
 *
 * Only the fields. The title, the description and the section's `x-actions`
 * row are the layout's, drawn from `useSchemaForm().form` where that layout
 * wants them — in the dialog, in a header the fields scroll under.
 *
 * A name no section carries draws nothing rather than throwing: `?tab=` is a
 * link someone shared before the application renamed a Struct field.
 */
export const SchemaSection = ({ name }: { name: string }) => {
  const { form } = useSchemaForm();
  const fields =
    name === LEADING
      ? form.sections
      : form.tabs.find((tab) => tab.name === name)?.fields;
  if (!fields?.length) return null;
  return <div className="flex flex-col gap-4">{fields.map(renderField)}</div>;
};

/**
 * What `query` found, across every section, grouped and in declaration order.
 *
 * The matches are the real fields, bound to the same form by their own paths,
 * so editing one and saving works exactly as it does in its section. Anything
 * else would be a second copy of the form with its own idea of the values.
 */
/**
 * Whether the section `name` has anything the user can change.
 *
 * The dialog asks it for the section on screen and draws "nothing to save
 * here" instead of a Save that would commit nothing -- see `hasEditable`
 * for what counts. An unknown name has nothing in it to edit.
 */
export const useSectionEditable = (name: string | undefined): boolean => {
  const { form } = useSchemaForm();
  return useMemo(() => {
    const fields =
      name === LEADING
        ? form.sections
        : form.tabs.find((tab) => tab.name === name)?.fields;
    return hasEditable(fields ?? []);
  }, [form, name]);
};

export const SchemaMatches = ({ query }: { query: string }) => {
  const { form } = useSchemaForm();
  const groups = useMemo(() => searchFields(form, query), [form, query]);

  if (groups.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        <Translator path="account.search.empty" />
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {groups.map((group) => (
        <div key={group.section.name} className="flex flex-col gap-4">
          <h3 className="text-sm font-medium text-muted-foreground">
            {group.section.name === LEADING ? (
              <Translator path="account.general" />
            ) : (
              group.section.title
            )}
          </h3>
          {group.fields.map(renderField)}
        </div>
      ))}
    </div>
  );
};

/** Save and Reset, or nothing at all when the page cannot be written to. */
export const SchemaSubmit = () => {
  const { readonly, isSubmitting } = useSchemaForm();
  const { reset } = useFormContext<FieldValues>();

  if (readonly) return null;

  return (
    <div className="flex gap-2">
      <Button type="submit" disabled={isSubmitting}>
        <Translator path="account.actions.save" />
      </Button>
      <Button type="button" variant="outline" onClick={() => reset()}>
        <Translator path="common.actions.reset" />
      </Button>
    </div>
  );
};

export default SchemaForm;
