import { cn } from '@/lib/utils';
import capitalize from 'lodash/capitalize';
import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { type IAccountAction, useAuth } from '@chainlit/react-client';

import Alert from '@/components/Alert';
import Icon from '@/components/Icon';
import {
  LEADING,
  SchemaMatches,
  SchemaSection,
  SchemaSubmit,
  useSchemaForm,
  useSectionEditable
} from '@/components/SchemaForm';
import { ActionButtons } from '@/components/SchemaForm/Cards';
import { useTranslation } from '@/components/i18n/Translator';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Input } from '@/components/ui/input';

import { useIsMobile } from '@/hooks/use-mobile';

/** A row of the menu: the leading scalars, or one of the schema's tabs. */
interface Section {
  name: string;
  title: string;
  description?: string;
  icon?: string;
  actions?: IAccountAction[];
}

/**
 * The settings-dialog layout: sections on the left, one section on the right.
 *
 * It lives inside `SchemaForm`, which is a provider rather than a layout, so
 * everything drawn here is bound to the one form the dialog submits — the
 * search results included. Nothing below holds a copy of a value; the only
 * state it owns is which section is on screen (in `?tab=`, so it is linkable)
 * and what the user typed into the search box.
 */
export default function AccountLayout() {
  const { form, readonly, onAction } = useSchemaForm();
  const { user } = useAuth();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState('');

  const sections = useMemo<Section[]>(() => {
    const rows: Section[] = [];
    // msgspec gives the top-level scalars no name of their own, and they are
    // what the user sees first, so the menu names them.
    if (form.sections.length > 0) {
      rows.push({ name: LEADING, title: t('account.general') });
    }
    for (const tab of form.tabs) {
      rows.push({
        name: tab.name,
        title: tab.title,
        description: tab.description,
        icon: tab.icon,
        actions: tab.actions
      });
    }
    return rows;
  }, [form, t]);

  // `?tab=` may name a section the application has since renamed or dropped;
  // the dialog opens on the first one rather than on nothing.
  const wanted = searchParams.get('tab');
  const active = sections.find((row) => row.name === wanted) ?? sections[0];
  const trimmed = query.trim();
  const editable = useSectionEditable(active?.name);

  const choose = (name: string) => {
    // A section the user picked is a section they want to read: leaving the
    // query in place would show them the matches instead.
    setQuery('');
    setSearchParams(
      (previous) => {
        // A copy of the previous params, so a `?tab=` change keeps whatever
        // else the address carries.
        const next = new URLSearchParams(previous);
        next.set('tab', name);
        return next;
      },
      // Replace, not push: Back leaves the account rather than walking
      // backwards through the sections the user looked at.
      { replace: true }
    );
  };

  const searchBox = (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={t('account.search.placeholder')}
        aria-label={t('account.search.placeholder')}
        className="h-9 pl-8"
        onKeyDown={(event) => {
          // The box sits inside the `<form>` the account is saved with, where
          // Enter is a submit: finishing a query would save the account.
          if (event.key === 'Enter') event.preventDefault();
        }}
      />
    </div>
  );

  const displayName = user?.display_name || user?.identifier || '';

  const row = (section: Section, className: string) => (
    <button
      key={section.name}
      type="button"
      // `type="button"` is load-bearing: the default inside a `<form>` is
      // submit, so picking a section would save the account on the way.
      onClick={() => choose(section.name)}
      aria-current={section.name === active?.name ? 'page' : undefined}
      className={cn(
        className,
        section.name === active?.name ? 'bg-accent' : 'hover:bg-accent/50'
      )}
    >
      <Icon name={section.icon || 'settings-2'} className="size-4 shrink-0" />
      <span className="truncate">{section.title}</span>
    </button>
  );

  return (
    <>
      {isMobile ? (
        // The menu becomes a strip. `pr-12` keeps it out from under the ×,
        // which is positioned against the dialog, not against this column.
        <div className="flex flex-col gap-2 border-b p-3 pr-12">
          {searchBox}
          <div className="flex gap-2 overflow-x-auto whitespace-nowrap">
            {sections.map((section) =>
              row(
                section,
                'flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-sm'
              )
            )}
          </div>
        </div>
      ) : (
        <div className="flex w-60 shrink-0 flex-col border-r">
          <div className="p-3">{searchBox}</div>
          <div className="flex items-center gap-3 px-3 pb-3">
            <Avatar className="size-9">
              <AvatarImage src={user?.metadata?.image} alt="user image" />
              <AvatarFallback className="bg-primary text-primary-foreground font-semibold">
                {capitalize(displayName[0])}
              </AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-col">
              <p className="truncate text-sm font-medium leading-none">
                {displayName}
              </p>
              <p className="truncate text-sm text-muted-foreground">
                {user?.identifier}
              </p>
            </div>
          </div>
          <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto p-2">
            {sections.map((section) =>
              row(
                section,
                'flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm'
              )
            )}
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-start justify-between gap-4 border-b px-6 py-4 pr-12">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold">{active?.title}</h2>
            {active?.description ? (
              <p className="text-sm text-muted-foreground">
                {active.description}
              </p>
            ) : null}
          </div>
          <ActionButtons
            actions={active?.actions}
            path={active?.name ?? ''}
            item={null}
            onAction={onAction}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {trimmed ? (
            <SchemaMatches query={trimmed} />
          ) : active ? (
            <SchemaSection name={active.name} />
          ) : null}
        </div>

        <div className="border-t px-6 py-3">
          {readonly ? (
            <Alert variant="info">{t('account.readonly')}</Alert>
          ) : !trimmed && !editable ? (
            // Per section, unlike the line above, which is about the page: a
            // feed of read-outs under "Save" promises that something on it
            // can be changed. Not while searching -- the matches come from
            // every section, and a draft left in another one is still saved
            // from here.
            <Alert variant="info">{t('account.nothingToSave')}</Alert>
          ) : (
            <SchemaSubmit />
          )}
        </div>
      </div>
    </>
  );
}
