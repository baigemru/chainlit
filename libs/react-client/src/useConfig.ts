import { useEffect } from 'react';
import { useRecoilState, useRecoilValue } from 'recoil';

import { useApi, useAuth } from './api';
import { chatProfileState, configState } from './state';
import { IChainlitConfig } from './types';

const useConfig = () => {
  const [config, setConfig] = useRecoilState(configState);
  const { isAuthenticated } = useAuth();
  const chatProfile = useRecoilValue(chatProfileState);
  const language = navigator.language || 'en-US';

  // Keyed on the profile, so a profile change fetches that profile's
  // config -- and the config on screen stays until the new one arrives.
  // It used to be blanked in between, which unmounted everything gated on
  // it: the thread page's resume among them, which then mounted again and
  // resumed the thread a second time, on a second session.
  const apiUrl = isAuthenticated
    ? `/project/settings?language=${language}${chatProfile ? `&chat_profile=${encodeURIComponent(chatProfile)}` : ''}`
    : null;

  const { data, error, isLoading } = useApi<IChainlitConfig>(apiUrl, {
    revalidateOnFocus: false,
    revalidateIfStale: false
  });

  useEffect(() => {
    if (!data) return;
    setConfig(data);
  }, [data, setConfig]);

  return { config, error, isLoading, language };
};

export { useConfig };
