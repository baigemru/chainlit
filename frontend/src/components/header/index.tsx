import { memo } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAuth, useConfig } from '@chainlit/react-client';

import ButtonLink from '@/components/ButtonLink';
import { useSidebar } from '@/components/ui/sidebar';

import ApiKeys from './ApiKeys';
import ChatProfiles from './ChatProfiles';
import NewChatButton from './NewChat';
import ReadmeButton from './Readme';
import ShareButton from './Share';
import SidebarTrigger from './SidebarTrigger';
import { ThemeToggle } from './ThemeToggle';
import UserNav from './UserNav';

const Header = memo(() => {
  const navigate = useNavigate();
  const { data, user } = useAuth();
  const { config } = useConfig();
  const { open, openMobile, isMobile } = useSidebar();

  const sidebarOpen = isMobile ? openMobile : open;

  const historyEnabled = data?.requireLogin && config?.dataPersistence;
  const sidebarHidden = config?.ui?.default_sidebar_state === 'hidden';

  const links = (config?.ui?.header_links || []).filter(
    (link) => !link.authenticated_only || !!user
  );

  return (
    <div
      className="p-3 flex h-[60px] items-center justify-between gap-2 relative"
      id="header"
    >
      <div className="flex items-center">
        {historyEnabled && !sidebarHidden ? (
          !sidebarOpen ? (
            <SidebarTrigger />
          ) : null
        ) : null}
        {historyEnabled && !sidebarHidden ? (
          !sidebarOpen ? (
            <NewChatButton navigate={navigate} />
          ) : null
        ) : (
          <NewChatButton navigate={navigate} />
        )}

        <ChatProfiles navigate={navigate} />
      </div>

      <div />
      <div className="flex items-center gap-1">
        <ShareButton />
        <ReadmeButton />
        <ApiKeys />
        {links &&
          links.map((link, index) => (
            <ButtonLink
              key={`${link.name}-${link.url}-${index}`}
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
          ))}
        <ThemeToggle />
        <UserNav />
      </div>
    </div>
  );
});

export { Header };
