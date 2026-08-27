import { useEffect, useRef } from 'react';
import { useRecoilState, useRecoilValue } from 'recoil';

import { useApi, useAuth } from './api';
import { chatProfileState, configState } from './state';
import { IChainlitConfig } from './types';

const useConfig = () => {
  const [config, setConfig] = useRecoilState(configState);
  const { isAuthenticated } = useAuth();
  const chatProfile = useRecoilValue(chatProfileState);
  const language = navigator.language || 'en-US';
  const prevChatProfileRef = useRef(chatProfile);

  // Build the API URL with optional chat profile parameter
  const apiUrl = isAuthenticated
    ? `/project/settings?language=${language}${chatProfile ? `&chat_profile=${encodeURIComponent(chatProfile)}` : ''}`
    : null;

  const hotSwap = !!config?.features?.hot_swap_chat_profile;

  // SWR is keyed on the URL, which already carries the chat profile, so a
  // profile change refetches on its own. Blanking the config below is only
  // how the legacy path turns `shouldFetch` back on — on the hot-swap path
  // that blank would unmount the whole app (App.tsx returns null without a
  // config), flip chatProfileOk and re-run the connect effect, tearing down
  // the very socket the swap exists to keep. So keep fetching instead.
  const shouldFetch = isAuthenticated && (!config || hotSwap);

  const { data, error, isLoading } = useApi<IChainlitConfig>(
    shouldFetch ? apiUrl : null
  );

  useEffect(() => {
    if (!data) return;
    setConfig(data);
  }, [data, setConfig]);

  // Clear config when chat profile changes to force re-fetch. Skipped on
  // the hot-swap path, where the refetch is already driven by the SWR key
  // and the old config stays on screen until the new one lands.
  useEffect(() => {
    if (prevChatProfileRef.current !== chatProfile) {
      if (!hotSwap) setConfig(undefined);
      prevChatProfileRef.current = chatProfile;
    }
  }, [chatProfile, setConfig, hotSwap]);

  return { config, error, isLoading, language };
};

export { useConfig };
