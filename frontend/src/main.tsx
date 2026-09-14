import AppWrapper from 'AppWrapper';
import { apiClient } from 'api';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { RecoilRoot } from 'recoil';

import { ChainlitContext, sessionDescriptorSeed } from '@chainlit/react-client';

import './index.css';

import { i18nSetupLocalization } from './i18n';
import getRouterBasename from './lib/router';
import { threadIdFromLocation } from './lib/threadAddress';
import { registerServiceWorker } from './registerServiceWorker';

i18nSetupLocalization();

// Which conversation this page load is asking for, decided before RecoilRoot
// mounts. The descriptor is seeded from it synchronously, so by the time
// `AutoResumeThread` compares the URL's thread with the descriptor's on its
// first commit they already agree -- an answer that arrived one effect later
// would have it clear the session the server had just kept.
//
// `window.location.pathname` still carries the basename here; the router is
// not mounted, so nothing has stripped it yet.
sessionDescriptorSeed.threadId = () =>
  threadIdFromLocation(window.location.pathname, getRouterBasename());

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <ChainlitContext.Provider value={apiClient}>
      <RecoilRoot>
        <AppWrapper />
      </RecoilRoot>
    </ChainlitContext.Provider>
  </React.StrictMode>
);

registerServiceWorker();
