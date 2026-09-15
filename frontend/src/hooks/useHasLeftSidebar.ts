import { useAuth, useConfig } from '@chainlit/react-client';

/**
 * Whether the thread-history sidebar exists on this screen at all. Three
 * places need the answer — the page that mounts it, the header that offers its
 * trigger, and the composer's chevron — and the two copies this replaced had
 * already started to drift apart in the order they read their two hooks.
 *
 * History needs both a login and somewhere to persist to; `"hidden"` is the
 * deployment saying it wants neither the sidebar nor anything that opens it.
 */
export const useHasLeftSidebar = (): boolean => {
  const { data } = useAuth();
  const { config } = useConfig();

  return Boolean(
    data?.requireLogin &&
    config?.dataPersistence &&
    config?.ui?.default_sidebar_state !== 'hidden'
  );
};
