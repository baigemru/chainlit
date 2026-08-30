import { MoreHorizontal } from 'lucide-react';
import { memo } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAuth, useConfig } from '@chainlit/react-client';

import ButtonLink from '@/components/ButtonLink';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { useSidebar } from '@/components/ui/sidebar';

import { useIsMobile } from '@/hooks/use-mobile';

import ApiKeys from './ApiKeys';
import ChatProfiles from './ChatProfiles';
import NewChatButton from './NewChat';
import ReadmeButton from './Readme';
import ShareButton from './Share';
import SidebarTrigger from './SidebarTrigger';
import { ThemeToggle } from './ThemeToggle';
import UserNav from './UserNav';

/** The built-in header buttons, in the order the header places them. */
export const HEADER_ITEMS = [
  'new_chat',
  'chat_profiles',
  'share',
  'readme',
  'api_keys',
  'theme',
  'user_nav'
] as const;

/** Mirrors `DEFAULT_MOBILE_HEADER` in `backend/chainlit/config.py`. */
export const DEFAULT_MOBILE_HEADER: string[] = [
  'new_chat',
  'chat_profiles',
  'user_nav'
];

/**
 * Whether a built-in button keeps its place in the header of a narrow
 * screen. Everything else moves into the overflow menu — including, if the
 * config says so, the user menu, which is why the overflow is a button of
 * its own and not a section of the avatar's dropdown.
 */
export function staysInHeader(name: string, mobileHeader?: string[]): boolean {
  return (mobileHeader ?? DEFAULT_MOBILE_HEADER).includes(name);
}

const Header = memo(() => {
  const navigate = useNavigate();
  const { data, user } = useAuth();
  const { config } = useConfig();
  const { open, openMobile, isMobile: sidebarIsMobile } = useSidebar();
  // The layout is decided by the viewport, never by the `device` label: the
  // label picks what is offered, the width picks how it is drawn.
  const isMobile = useIsMobile();

  const sidebarOpen = sidebarIsMobile ? openMobile : open;

  const historyEnabled = data?.requireLogin && config?.dataPersistence;
  const sidebarHidden = config?.ui?.default_sidebar_state === 'hidden';
  const inSidebar = Boolean(historyEnabled && !sidebarHidden);
  const showNewChat = inSidebar ? !sidebarOpen : true;

  const links = (config?.ui?.header_links || []).filter(
    (link) => !link.authenticated_only || !!user
  );
  const pinnedLinks = isMobile
    ? links.filter((link) => link.collapse_on_mobile === false)
    : links;
  const collapsedLinks = isMobile
    ? links.filter((link) => link.collapse_on_mobile !== false)
    : [];

  const mobileHeader = config?.ui?.mobile_header;
  const stays = (name: string) =>
    !isMobile || staysInHeader(name, mobileHeader);

  // Whether a collapsed button would draw anything at all — each of them
  // hides itself when its feature is off, and an overflow button that opens
  // an empty menu is worse than no overflow button.
  const drawn: Record<string, boolean> = {
    new_chat: showNewChat,
    chat_profiles: (config?.chatProfiles?.length ?? 0) > 1,
    share: Boolean(config?.dataPersistence && (config as any)?.threadSharing),
    readme: !!config?.markdown,
    api_keys: !!config?.userEnv?.length,
    theme: true,
    user_nav: !!user
  };
  const hasOverflow =
    collapsedLinks.length > 0 ||
    HEADER_ITEMS.some((name) => !stays(name) && drawn[name]);

  const renderLink = (
    link: (typeof links)[number],
    index: number,
    prefix: string
  ) => (
    <ButtonLink
      key={`${prefix}-${link.name}-${link.url}-${index}`}
      name={link.name}
      displayName={link.display_name}
      iconUrl={link.icon_url}
      iconUrlLight={link.icon_url_light}
      iconUrlDark={link.icon_url_dark}
      iconMask={link.icon_mask}
      url={link.url}
      target={link.target}
      labelUrl={link.label_url}
      labelRefreshInterval={link.label_refresh_interval}
    />
  );

  return (
    <div
      className="p-3 flex h-[60px] items-center justify-between gap-2 relative"
      id="header"
    >
      <div className="flex items-center">
        {inSidebar && !sidebarOpen ? <SidebarTrigger /> : null}
        {showNewChat && stays('new_chat') ? (
          <NewChatButton navigate={navigate} />
        ) : null}

        {stays('chat_profiles') ? <ChatProfiles navigate={navigate} /> : null}
      </div>

      <div />
      <div className="flex items-center gap-1">
        {stays('share') ? <ShareButton /> : null}
        {stays('readme') ? <ReadmeButton /> : null}
        {stays('api_keys') ? <ApiKeys /> : null}
        {pinnedLinks.map((link, index) => renderLink(link, index, 'header'))}
        {stays('theme') ? <ThemeToggle /> : null}
        {stays('user_nav') ? <UserNav /> : null}
        {hasOverflow ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                id="header-overflow-button"
                size="icon"
                variant="ghost"
                className="text-muted-foreground hover:text-muted-foreground"
              >
                <MoreHorizontal className="!size-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[12rem]">
              {/* The buttons that own a dialog of their own are rendered as
                  themselves, not as menu rows: their dialog is mounted here
                  and a row that closed the menu would take it down with it. */}
              {showNewChat && !stays('new_chat') ? (
                <div className="flex items-center px-1 py-0.5">
                  <NewChatButton navigate={navigate} />
                </div>
              ) : null}
              {!stays('chat_profiles') ? (
                <div className="flex items-center px-1 py-0.5">
                  <ChatProfiles navigate={navigate} />
                </div>
              ) : null}
              {!stays('share') ? <ShareButton collapsed /> : null}
              {!stays('readme') ? <ReadmeButton collapsed /> : null}
              {!stays('api_keys') ? <ApiKeys collapsed /> : null}
              {collapsedLinks.map((link, index) => (
                <div
                  key={`overflow-${link.name}-${link.url}-${index}`}
                  className="flex items-center px-1 py-0.5"
                >
                  {renderLink(link, index, 'overflow')}
                </div>
              ))}
              {!stays('theme') ? <ThemeToggle collapsed /> : null}
              {!stays('user_nav') ? <UserNav collapsed /> : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
    </div>
  );
});

export { Header };
