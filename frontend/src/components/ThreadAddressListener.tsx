import { threadAddressFor } from '@/lib/threadAddress';
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useChatTransport, useConfig } from '@chainlit/react-client';

/**
 * Brings the address bar into line with the thread the server named.
 *
 * The URL is the request and `session.ready` is the answer: a chat begun at
 * `/` gets its address the moment the server names the thread, and a resume
 * the server would not grant comes back as a *different* thread — no error
 * frame any more — so the address has to follow rather than the user being
 * bounced home with a toast.
 *
 * `navigate`, not `history.replaceState`: react-router's browser history
 * subscribes to `popstate` only (`@remix-run/router`), so a bare
 * `replaceState` changes the bar and leaves `useParams()` pointing at the
 * previous thread — which is precisely the disagreement AutoResumeThread
 * answers by clearing the session.
 *
 * No `flushSync` here, unlike ChatProfileSwitchListener. Frames reach the
 * sink before the listeners, so `currentThreadId` is already the new thread
 * in the same commit as the navigation; and the intermediate state, were the
 * commit ever split, is harmless — on `/thread/<old>` AutoResumeThread's
 * guard is satisfied (`idToResume === id`) and on `/` it is not mounted at
 * all. The switch listener needed the flush because its intermediate had the
 * thread *cleared*, which is the state that resumes the old thread over the
 * new chat.
 */
export default function ThreadAddressListener() {
  const transport = useChatTransport();
  const { config } = useConfig();
  const navigate = useNavigate();
  const location = useLocation();

  // Read through refs: the subscription below registers once per transport
  // and outlives every socket, so a captured pathname would be the one this
  // component mounted with — a reconnect while the user reads a `/share/`
  // page would then be judged against the wrong route and navigate them off
  // it.
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const pathnameRef = useRef(location.pathname);
  pathnameRef.current = location.pathname;
  const resumableRef = useRef(false);
  resumableRef.current = !!config?.threadResumable;

  useEffect(() => {
    return transport.onMessage((message) => {
      if (message.t !== 'session.ready' || !message.threadId) return;
      // Only an app that can resume gets a live address. The server names a
      // thread for every session from its first frame, but an app without
      // resume hooks starts over on a reload (the server disowns whatever
      // thread the address offers), and `Thread.tsx` shows `/thread/<id>`
      // as a read-only view for such an app -- a reload there would land
      // the user on a frozen copy of the chat they were just typing in, or
      // on a 404 for a thread that has no row yet.
      if (!resumableRef.current) return;
      // `''`, not the basename: `useLocation().pathname` is already stripped
      // of it (`<Router>` publishes `stripBasename(pathname, basename)`),
      // and `navigate` joins it back on the way out.
      const target = threadAddressFor(
        pathnameRef.current,
        '',
        message.threadId
      );
      if (!target) return;
      // Replace, not push: this is the same conversation the user is already
      // looking at being named, so Back must still lead where they came from.
      navigateRef.current(target, { replace: true });
    });
  }, [transport]);

  return null;
}
