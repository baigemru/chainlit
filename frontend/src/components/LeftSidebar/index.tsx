import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import SidebarTrigger from '@/components/header/SidebarTrigger';
import {
  Sidebar,
  SidebarHeader,
  SidebarRail,
  useSidebar
} from '@/components/ui/sidebar';

import NewChatButton from '../header/NewChat';
import PinnedAccount from './PinnedAccount';
import SearchChats from './Search';
import { ThreadHistory } from './ThreadHistory';

export default function LeftSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const navigate = useNavigate();
  const { setOpenMobile } = useSidebar();
  const { key } = useLocation();

  // On a phone the sidebar is a modal sheet, and everything inside it that
  // opens a chat — a thread, a search hit, the delete that walks away from the
  // thread it removed — used to leave the sheet standing over the chat it had
  // just opened. One effect on the navigation covers all of them, including
  // `/thread/a` → `/thread/b`, which no route remount ever caught. The call
  // sites stay ignorant of the sheet; the one that does not navigate at all
  // (a new chat started from `/`) closes it itself, in `NewChat`.
  //
  // Keyed on `key`, not on `pathname`: the router mints a fresh location key
  // for every navigation, a push to the address already shown included, and
  // that case is reachable — `ThreadList` links the row of the thread you are
  // already in to `''`, so on `pathname` a tap on it did nothing whatsoever.
  // A re-render for any other reason leaves the key alone, which is the guard
  // a bare `useEffect` with no deps would lose. The mount run writes `false`
  // over `false` and React bails out of it.
  //
  // No `isMobile` guard: `openMobile` is unread on desktop, so the guard could
  // only ever be wrong.
  useEffect(() => {
    setOpenMobile(false);
  }, [key, setOpenMobile]);

  return (
    <Sidebar {...props} className="border-none">
      <SidebarHeader className="py-3">
        <div className="flex items-center justify-between">
          <SidebarTrigger />
          <div className="flex items-center">
            <SearchChats />
            <NewChatButton navigate={navigate} />
          </div>
        </div>
      </SidebarHeader>
      {/* Outside `ThreadHistory`'s `SidebarContent`, and deliberately: that
          one is the scrolling region, and a pinned destination that scrolls
          away with the chat list is not pinned. Renders nothing at all when
          the application pinned nothing, so a deployment without an account
          page has the header sitting on the history exactly as before. */}
      <PinnedAccount />
      <ThreadHistory />
      <SidebarRail />
    </Sidebar>
  );
}
