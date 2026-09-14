import { useContext } from 'react';
import { ChainlitContext } from 'src/index';
import { sessionIdStorage } from 'src/state';

import { useAuthState } from './state';

export const useSessionManagement = () => {
  const apiClient = useContext(ChainlitContext);
  const { setUser, setThreadHistory } = useAuthState();

  const logout = async (reload = false): Promise<void> => {
    await apiClient.logout();
    setUser(undefined);
    setThreadHistory(undefined);

    // The reload below is a *reload* navigation, which is exactly the one
    // that adopts the stored id. Whoever logs in next in this tab must not
    // offer the previous user's session. The thread is not this function's
    // business: it lives in the address bar, and the caller is the one with
    // a router (UserNav navigates home before calling this).
    try {
      sessionStorage.removeItem(sessionIdStorage.key);
    } catch (_error) {
      // Storage unavailable: nothing was stored to leak either.
    }

    if (reload) {
      window.location.reload();
    }
  };

  return { logout };
};
