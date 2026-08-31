import { Navigate } from 'react-router-dom';
import { useRecoilValue } from 'recoil';

import { sideViewState, useAuth, useConfig } from '@chainlit/react-client';

import ChatProfileSwitchListener from '@/components/ChatProfileSwitchListener';
import ElementSideView from '@/components/ElementSideView';
import LeftSidebar from '@/components/LeftSidebar';
import MobileNotice from '@/components/MobileNotice';
import { TaskList } from '@/components/Tasklist';
import ThreadReturnListener from '@/components/ThreadReturnListener';
import { Header } from '@/components/header';
import { ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';

import { userEnvState } from 'state/user';

type Props = {
  children: JSX.Element;
};

const Page = ({ children }: Props) => {
  const { config } = useConfig();
  const { data } = useAuth();
  const userEnv = useRecoilValue(userEnvState);
  const sideView = useRecoilValue(sideViewState);

  if (config?.userEnv) {
    for (const key of config.userEnv || []) {
      if (!userEnv[key]) return <Navigate to="/env" />;
    }
  }

  const mainContent = (
    <div className="flex flex-col h-full w-full">
      <Header />
      <ResizablePanelGroup
        direction="horizontal"
        className="flex flex-row flex-grow"
      >
        <ResizablePanel
          className="flex flex-col h-full w-full"
          minSize={40}
          defaultSize={50}
        >
          <div className="flex flex-row flex-grow overflow-auto">
            {children}
          </div>
        </ResizablePanel>
        {sideView ? <ElementSideView /> : <TaskList isMobile={false} />}
      </ResizablePanelGroup>
    </div>
  );

  const historyEnabled = config?.dataPersistence && data?.requireLogin;
  const sidebarHidden = config?.ui?.default_sidebar_state === 'hidden';

  // `viewport-fit=cover` in index.html is global, so on a notched phone the
  // viewport runs under the sensor housing in every orientation. The composer
  // already compensates for the bottom inset; the left and right ones only bite
  // in landscape, where the housing eats the shell's leading or trailing edge.
  // Both resolve to 0 in portrait and on desktop, so nothing moves there.
  return (
    <SidebarProvider
      className="pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]"
      defaultOpen={config?.ui.default_sidebar_state !== 'closed'}
    >
      <ChatProfileSwitchListener />
      <MobileNotice />
      <ThreadReturnListener />
      {historyEnabled && !sidebarHidden ? (
        <>
          <LeftSidebar />
          <SidebarInset className="max-h-svh min-w-0">
            {mainContent}
          </SidebarInset>
        </>
      ) : (
        // `w-full`, not `w-screen`: 100vw ignores the safe-area padding on the
        // wrapper above and would overhang it by the inset in landscape.
        <div className="h-screen w-full flex">{mainContent}</div>
      )}
    </SidebarProvider>
  );
};

export default Page;
