import { useContext } from 'react';
import { ChainlitContext } from 'src/index';
import { sessionIdStorage, threadIdStorageKey } from 'src/state';

import { useAuthState } from './state';

export const useSessionManagement = () => {
  const apiClient = useContext(ChainlitContext);
  const { setUser, setThreadHistory } = useAuthState();

  const logout = async (reload = false): Promise<void> => {
    await apiClient.logout();
    setUser(undefined);
    setThreadHistory(undefined);

    // The reload below is a *reload* navigation, which is exactly the one
    // that adopts both stored keys. Whoever logs in next in this tab must
    // not offer the previous user's session id, nor ask for their thread.
    try {
      sessionStorage.removeItem(sessionIdStorage.key);
      sessionStorage.removeItem(threadIdStorageKey());
    } catch (_error) {
      // Storage unavailable: nothing was stored to leak either.
    }

    if (reload) {
      window.location.reload();
    }
  };

  return { logout };
};
