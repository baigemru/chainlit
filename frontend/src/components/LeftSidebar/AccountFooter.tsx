import { Link, useLocation } from 'react-router-dom';
import { useRecoilValue } from 'recoil';

import { accountBadgeState, useAuth, useConfig } from '@chainlit/react-client';

import UserAvatar from '@/components/UserAvatar';
import { Badge } from '@/components/ui/badge';
import {
  SidebarFooter,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem
} from '@/components/ui/sidebar';
import { Translator } from 'components/i18n';

/**
 * The way to the account page, at the foot of the thread history.
 *
 * The avatar in the header is a menu handle first and a destination second,
 * and a page people are meant to live in should not be two clicks behind a
 * dropdown that mostly holds the door out. This row is the destination said
 * plainly: the same face, the same words, one click.
 *
 * Gated exactly as the row in `UserNav` is — an account page the application
 * declared, and somebody signed in to have one — and deliberately not as
 * `PinnedAccount` is: that block draws one row per *section* and so cannot
 * exist without the schema, while this row leads to the page itself and needs
 * to know nothing about what is on it.
 *
 * The count is written out here as well as dotted on the face. The dot says
 * "something is new"; the number says whether it is worth the click, and
 * this row is the one that is always on screen -- the user menu that also
 * carries the number is closed most of the time.
 */
export default function AccountFooter() {
  const { config } = useConfig();
  const { user } = useAuth();
  const { pathname } = useLocation();
  const badge = useRecoilValue(accountBadgeState);

  const account = config?.ui?.account;
  if (!account?.enabled || !user) return null;

  const displayName = user.display_name || user.identifier;
  const unseen = badge ?? 0;

  return (
    <SidebarFooter>
      <SidebarMenu>
        <SidebarMenuItem>
          {/* On the address and not on `?tab=`: the pinned rows above claim a
              section, this one claims the page, and it is lit for every
              section of it. */}
          <SidebarMenuButton asChild isActive={pathname === '/account'}>
            <Link to="/account">
              <UserAvatar
                displayName={displayName}
                image={user.metadata?.image}
                unseen={badge}
                // The row is the height of a menu button, not of the header's
                // avatar; the badge rides along at the smaller size.
                className="h-6 w-6 text-xs"
              />
              <span className="truncate">
                {account.title ? (
                  account.title
                ) : (
                  <Translator path="navigation.user.menu.account" />
                )}
              </span>
              {unseen > 0 ? (
                // `ml-auto` pins it to the row's right edge: the title may
                // truncate, the count may not.
                <Badge
                  variant="destructive"
                  data-testid="account-footer-count"
                  className="ml-auto shrink-0 px-1.5 py-0"
                >
                  {unseen}
                </Badge>
              ) : null}
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarFooter>
  );
}
