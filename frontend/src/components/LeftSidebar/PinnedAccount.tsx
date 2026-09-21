import { useMemo } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { useAuth, useConfig } from '@chainlit/react-client';

import Icon from '@/components/Icon';
import { resolveForm } from '@/components/SchemaForm/resolve';
import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator
} from '@/components/ui/sidebar';

/**
 * The account sections the application pinned, as rows above the chat history.
 *
 * The account is a dialog over the chat, reachable only through the user menu
 * behind the avatar — which is the right place for a settings page and the
 * wrong one for the two or three sections that are where the user's work
 * actually lives. A pinned row is the application saying "this section is a
 * destination, not a setting": `x-pinned: true` on the section property, the
 * same `Meta(extra_json_schema=...)` mechanism as `x-icon`.
 *
 * It renders off `ui.account.schema` — the schema the `/project/settings`
 * controller attaches to the config — rather than off `GET /project/account`,
 * because the sidebar draws on every page and that route is the occasion on
 * which the application marks things seen. Names and icons are public; values
 * are not, and none are read here.
 *
 * No ceiling on the number of rows, and no counts on them. How many sections
 * deserve the sidebar is the application's judgement about its own schema, not
 * something this component can enforce without deciding which one it silently
 * drops.
 */
export default function PinnedAccount() {
  const { config } = useConfig();
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [searchParams] = useSearchParams();

  const account = config?.ui?.account;
  const schema = account?.schema;

  // Above the guards below, because it is a hook; keyed on the schema, which
  // changes only when the config is refetched, so the sidebar's re-renders —
  // one per navigation — do not walk the whole account schema again.
  const tabs = useMemo(
    () => (schema ? resolveForm(schema).tabs.filter((tab) => tab.pinned) : []),
    [schema]
  );

  // The same three conditions the user menu's row answers to (`UserNav`): no
  // account page, no schema to read the sections out of, or nobody signed in
  // to have sections of their own.
  if (!account?.enabled || !schema || !user) return null;
  if (tabs.length === 0) return null;

  // Only on `/account` itself: `?tab=` survives in the address of a route that
  // has nothing to do with the account, and a row lit up behind a chat would
  // claim the dialog is open when it is not.
  const active = pathname === '/account' ? searchParams.get('tab') : null;

  return (
    <>
      <SidebarGroup>
        <SidebarMenu>
          {tabs.map((tab) => (
            <SidebarMenuItem key={tab.name}>
              <SidebarMenuButton asChild isActive={tab.name === active}>
                <Link to={`/account?tab=${tab.name}`}>
                  <Icon name={tab.icon || 'settings-2'} className="size-4" />
                  <span className="truncate">{tab.title}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroup>
      {/* The rule belongs to the block above it, not to the history below:
          drawn from the sidebar it would be a line under nothing wherever the
          application pinned no sections, and this component is the only place
          that knows whether there are any. */}
      <SidebarSeparator />
    </>
  );
}
