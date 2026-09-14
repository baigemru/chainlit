import { useContext } from 'react';
import { ChainlitContext } from 'src/index';

import { useAuthState } from './state';

export const useSessionManagement = () => {
  const apiClient = useContext(ChainlitContext);
  const { setUser, setThreadHistory } = useAuthState();

  const logout = async (reload = false): Promise<void> => {
    await apiClient.logout();
    setUser(undefined);
    setThreadHistory(undefined);

    // Nothing to forget here any more: this tab writes no session id down,
    // and the thread is not this function's business either — it lives in
    // the address bar, and the caller is the one with a router (UserNav
    // navigates home before calling this, so the reload below asks for a
    // new chat rather than for the thread the previous user was in).
    if (reload) {
      window.location.reload();
    }
  };

  return { logout };
};
