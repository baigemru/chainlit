import capitalize from 'lodash/capitalize';
import { LogOut } from 'lucide-react';
import { useState } from 'react';

import { useAuth, useConfig } from '@chainlit/react-client';

import IframeModal from '@/components/IframeModal';
import LinkIcon from '@/components/LinkIcon';
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
  const [iframeLink, setIframeLink] = useState<{
    name: string;
    url: string;
  } | null>(null);

  if (!user) return null;
  const displayName = user?.display_name || user?.identifier;
  const menuLinks = config?.ui?.user_menu_links || [];

  const items = (
    <>
      <DropdownMenuLabel className="font-normal">
        <div className="flex flex-col space-y-1">
          <p className="text-sm font-medium leading-none">{displayName}</p>
        </div>
      </DropdownMenuLabel>
      <DropdownMenuSeparator />
      {menuLinks.map((link, index) => {
        if (link.target === 'iframe') {
          return (
            <DropdownMenuItem
              key={`${link.name}-${index}`}
              // Collapsed, the modal is mounted inside the menu that this
              // row would close, so it would unmount on the way in.
              onSelect={collapsed ? (e) => e.preventDefault() : undefined}
              onClick={() =>
                setIframeLink({
                  name: link.display_name || link.name,
                  url: link.url
                })
              }
            >
              <span>{link.display_name || link.name}</span>
              <LinkIcon
                iconUrl={link.icon_url}
                iconUrlLight={link.icon_url_light}
                iconUrlDark={link.icon_url_dark}
                iconMask={link.icon_mask}
                className="ml-auto size-4"
              />
            </DropdownMenuItem>
          );
        }
        return (
          <DropdownMenuItem key={`${link.name}-${index}`} asChild>
            <a
              href={link.url}
              target={link.target ?? '_blank'}
              rel="noopener noreferrer"
            >
              <span>{link.display_name || link.name}</span>
              <LinkIcon
                iconUrl={link.icon_url}
                iconUrlLight={link.icon_url_light}
                iconUrlDark={link.icon_url_dark}
                iconMask={link.icon_mask}
                className="ml-auto size-4"
              />
            </a>
          </DropdownMenuItem>
        );
      })}
      {menuLinks.length > 0 && <DropdownMenuSeparator />}
      <DropdownMenuItem onClick={() => logout(true)}>
        <Translator path="navigation.user.menu.logout" />
        <LogOut className="ml-auto" />
      </DropdownMenuItem>
    </>
  );

  const modal = iframeLink && (
    <IframeModal
      open={true}
      onOpenChange={(open) => {
        if (!open) setIframeLink(null);
      }}
      title={iframeLink.name}
      url={iframeLink.url}
    />
  );

  if (collapsed) {
    return (
      <>
        {items}
        {modal}
      </>
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            id="user-nav-button"
            variant="ghost"
            className="relative h-8 w-8 rounded-full"
          >
            <Avatar className="h-8 w-8">
              <AvatarImage src={user?.metadata.image} alt="user image" />
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
      {modal}
    </>
  );
}
