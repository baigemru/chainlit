import { cn } from '@/lib/utils';
import { useEffect } from 'react';
import { RouterProvider } from 'react-router-dom';
import { useRecoilValue } from 'recoil';
import { router } from 'router';
import { toast } from 'sonner';

import {
  useAuth,
  useChatData,
  useChatSession,
  useConfig
} from '@chainlit/react-client';

import { ThemeProvider } from './components/ThemeProvider';
import { Loader } from '@/components/Loader';
import { Toaster } from '@/components/ui/sonner';

import { pickDefaultProfile, useDeviceKey } from '@/hooks/use-mobile';

import { userEnvState } from 'state/user';

declare global {
  interface Window {
    cl_shadowRootElement?: HTMLDivElement;
    theme?: {
      light: Record<string, string>;
      dark: Record<string, string>;
    };
  }
}

function App() {
  const { config } = useConfig();

  const { isAuthenticated, data, isReady } = useAuth();
  const userEnv = useRecoilValue(userEnvState);
  const { attach, descriptor, chatProfile, setChatProfile } = useChatSession();
  const { superseded } = useChatData();
  const device = useDeviceKey();

  const configLoaded = !!config;

  // The server reads the profile out of `hello` and a session is born with
  // it, so the first handshake must not go out before one is chosen.
  const chatProfileOk = configLoaded
    ? config.chatProfiles.length
      ? !!chatProfile
      : true
    : false;

  // The whole connection policy: when the app is ready to talk, say which
  // session it is talking about. Attaching is idempotent, so this effect
  // states an intent rather than performing a transition.
  useEffect(() => {
    if (!isAuthenticated || !isReady || !chatProfileOk) {
      return;
    }

    attach(descriptor, { userEnv });
  }, [userEnv, isAuthenticated, attach, descriptor, isReady, chatProfileOk]);

  // Close 4409: the session was taken over by another connection. Nothing is
  // broken and nothing is lost — this window simply no longer speaks for the
  // conversation, and the composer is already locked because of it.
  useEffect(() => {
    if (!superseded) return;
    toast.info('This chat was opened in another window.');
  }, [superseded]);

  useEffect(() => {
    if (
      !configLoaded ||
      !config ||
      !config.chatProfiles?.length ||
      chatProfile
    ) {
      return;
    }

    // Two hubs now claim `default`, one per device; the device decides which
    // of them this window boots into.
    setChatProfile(pickDefaultProfile(config.chatProfiles, device));
  }, [configLoaded, config, chatProfile, device, setChatProfile]);

  if (!configLoaded && isAuthenticated) return null;

  return (
    <ThemeProvider
      storageKey="vite-ui-theme"
      defaultTheme={data?.default_theme}
    >
      <Toaster richColors className="toast" position="top-right" />

      <RouterProvider router={router} />

      <div
        className={cn(
          'bg-[hsl(var(--background))] flex items-center justify-center fixed size-full p-2 top-0',
          isReady && 'hidden'
        )}
      >
        <Loader className="!size-6" />
      </div>
    </ThemeProvider>
  );
}

export default App;
