import capitalize from 'lodash/capitalize';
import { LogOut, UserRound } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { useAuth, useConfig } from '@chainlit/react-client';

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { Translator } from 'components/i18n';

interface Props {
  /**
   * Render the menu's rows straight into the header's overflow menu instead
   * of behind an avatar of its own.
   */
  collapsed?: boolean;
}

export default function UserNav({ collapsed }: Props) {
  const { user, logout } = useAuth();
  const { config } = useConfig();
  const navigate = useNavigate();

  if (!user) return null;
  const displayName = user?.display_name || user?.identifier;
  const account = config?.ui?.account;

  const items = (
    <>
      <DropdownMenuLabel className="font-normal">
        <div className="flex flex-col space-y-1">
          <p className="text-sm font-medium leading-none">{displayName}</p>
        </div>
      </DropdownMenuLabel>
      {/* The account page is the menu's one destination besides the door out:
          anything the application wants to offer here — the billing portal
          included — is a field on the Struct it registers with `@cl.account`,
          not a row of its own. */}
      {account?.enabled ? (
        <DropdownMenuItem onClick={() => navigate('/account')}>
          {account.title ? (
            <span>{account.title}</span>
          ) : (
            <Translator path="navigation.user.menu.account" />
          )}
          <UserRound className="ml-auto" />
        </DropdownMenuItem>
      ) : null}
      <DropdownMenuSeparator />
      <DropdownMenuItem
        onClick={() => {
          // Home first, then the logout's reload. The address bar is the
          // thread request now, so reloading on `/thread/<id>` would hand
          // the next person to log in here a request for the previous
          // user's conversation -- the server disowns it, but the address
          // must not carry it in the first place. Done here rather than in
          // `logout`: that hook lives in the react-client package, has no
          // router, and must not grow one. `logout` awaits the network
          // before it reloads, so the router's replaceState has landed long
          // before the page goes.
          navigate('/', { replace: true });
          logout(true);
        }}
      >
        <Translator path="navigation.user.menu.logout" />
        <LogOut className="ml-auto" />
      </DropdownMenuItem>
    </>
  );

  if (collapsed) {
    return items;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          id="user-nav-button"
          variant="ghost"
          className="relative h-8 w-8 rounded-full"
        >
          <Avatar className="h-8 w-8">
            {/* `GET /user` omits an empty `metadata` (msgspec
                `omit_defaults`), so a user with nothing in it arrives
                without the key at all. */}
            <AvatarImage src={user?.metadata?.image} alt="user image" />
            <AvatarFallback className="bg-primary text-primary-foreground font-semibold">
              {capitalize(displayName[0])}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-26" align="end" forceMount>
        {items}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
