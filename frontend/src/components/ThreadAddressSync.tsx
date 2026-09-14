import { threadAddressFor, threadIdFromLocation } from '@/lib/threadAddress';
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useRecoilValue } from 'recoil';
import { toast } from 'sonner';

import {
  currentThreadIdState,
  useChatData,
  useChatInteract,
  useChatSession,
  useChatTransport,
  useConfig
} from '@chainlit/react-client';

import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { openThreadTransitionState } from '@/state/chat';

/**
 * The one place the address bar and the session answer each other.
 *
 * Both directions live here because they are two halves of one rule — the
 * URL asks, `session.ready` answers — and splitting them is what produced
 * the loop this replaces: `AutoResumeThread` asked, mounted under
 * `/thread/:id`, while `ThreadAddressListener` answered from `Page`, so any
 * commit in which the route had moved but the session had not read as a
 * request for the thread the session was already being given.
 *
 * **Asking.** When the route names a thread that is neither the one the
 * session is in nor the one the descriptor already asked for, `clear()`
 * gives this session up and opens one for that thread. Both guards are
 * needed: the descriptor is the request — seeded from the URL before
 * `RecoilRoot` mounts, and written by the `clear` below afterwards —
 * `currentThreadId` is the server's answer, and dropping either turns an
 * ordinary navigation into a second clear against a session that was just
 * created.
 *
 * **Answering.** `session.ready` names the thread the server actually put
 * the session in, and the address follows with `replace`: a chat begun at
 * `/` gets its name, and a resume the server would not grant comes back as a
 * *different* thread rather than as an error.
 *
 * `navigate`, not `history.replaceState`: react-router's browser history
 * subscribes to `popstate` only (`@remix-run/router`), so a bare
 * `replaceState` changes the bar and leaves `useParams()` pointing at the
 * previous thread — which this component would then read as a request.
 *
 * No `flushSync` on the answering side. The intermediate that would hurt is
 * `/thread/<new>` committed while `currentThreadId` is still undefined: the
 * asking effect would see a route naming a thread nobody asked for and clear
 * the session the server had just created. It cannot be committed. The
 * `currentThreadId` write is issued from the session sink, which runs before
 * any `onMessage` listener for the same frame (`transport.ts` `dispatch`),
 * and the navigation is issued after it at no higher priority: `navigate` is
 * not wrapped in `flushSync`, and RouterProvider's subscriber calls
 * `setStateImpl` plainly unless `future.v7_startTransition` is on, in which
 * case it wraps it in `React.startTransition` — a *lower* lane
 * (`react-router@6.30.6/dist/react-router.development.js:911-917`; this app
 * passes no `future`, so it takes the plain branch). Either way React cannot
 * render the later update without the earlier one.
 */
export default function ThreadAddressSync() {
  const transport = useChatTransport();
  const { config } = useConfig();
  const { clear } = useChatInteract();
  const { idToResume } = useChatSession();
  const { error } = useChatData();
  const navigate = useNavigate();
  const location = useLocation();
  const resetKeptTranscript = useResetKeptTranscript();
  const currentThreadId = useRecoilValue(currentThreadIdState);

  // `threadIdFromLocation`, not `useParams`: this is mounted by `Page` for
  // every route, and only `/thread/:id` is a request. `/share/:id` and
  // `/element/:id` are views of something other than this session and yield
  // undefined here, exactly as they do in the answering direction.
  const routeThreadId = threadIdFromLocation(location.pathname, '');
  const resumable = !!config?.threadResumable;

  // Read through refs: these are actions, not inputs. In the deps array they
  // would re-run the effect whenever an unrelated atom moved one of the
  // setters they close over, and a re-run is a second `clear()`.
  const clearRef = useRef(clear);
  clearRef.current = clear;
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const resetKeptTranscriptRef = useRef(resetKeptTranscript);
  resetKeptTranscriptRef.current = resetKeptTranscript;
  // Same reason: the transition does not trigger a resume, it only tells a
  // return (which keeps the transcript on screen) from a plain open out of
  // the history (which starts blank).
  const transition = useRecoilValue(openThreadTransitionState);
  const transitionRef = useRef(transition);
  transitionRef.current = transition;
  const persistent = !!config?.dataPersistence;
  const persistentRef = useRef(persistent);
  persistentRef.current = persistent;

  // ---- the URL asks -------------------------------------------------
  useEffect(() => {
    if (!resumable || !routeThreadId) return;
    // Already in it, or already asking for it. The second half is what makes
    // this safe to re-run: the profile-keyed config is refetched the moment a
    // resume names the thread's profile, and the re-run must be a no-op.
    if (routeThreadId === currentThreadId) return;
    if (routeThreadId === idToResume) return;

    const isReturn =
      transitionRef.current?.threadId === routeThreadId &&
      transitionRef.current.keepTranscript;
    if (!isReturn) resetKeptTranscriptRef.current();
    // One write: the successor session and the thread it resumes are the
    // same decision.
    clearRef.current({ threadId: routeThreadId });
    if (!persistentRef.current) navigateRef.current('/');
  }, [resumable, routeThreadId, currentThreadId, idToResume]);

  // ---- a resume that failed outright --------------------------------
  useEffect(() => {
    if (!resumable || !routeThreadId) return;
    // Only while the resume is outstanding. Gated on the descriptor because
    // on the commit that issues it the request has been made but not
    // rendered, and an error left over from the session `clear()` just
    // dropped would be read as this resume's answer; gated on the thread not
    // being current because once the server has answered, a later transport
    // error is a dead connection, not a refusal — and throwing the user out
    // of the chat they are reading for it would be absurd.
    if (routeThreadId !== idToResume || routeThreadId === currentThreadId) {
      return;
    }
    // `error` does not cover close 4409: a session taken over by another
    // window is not a resume that failed, and sending the user home for it
    // would throw away the thread they asked for.
    if (!error) return;
    toast.error("Couldn't resume chat");
    // The descriptor is the guard above, so a resume that failed has to let
    // go of the thread it was for -- otherwise picking the same thread out
    // of the history again would find the guard already satisfied and do
    // nothing at all.
    clearRef.current();
    navigateRef.current('/');
  }, [resumable, error, routeThreadId, idToResume, currentThreadId]);

  // ---- `session.ready` answers --------------------------------------

  // Read through refs: the subscription below registers once per transport
  // and outlives every socket, so a captured pathname would be the one this
  // component mounted with — a reconnect while the user reads a `/share/`
  // page would then be judged against the wrong route and navigate them off
  // it.
  const pathnameRef = useRef(location.pathname);
  pathnameRef.current = location.pathname;
  const resumableRef = useRef(resumable);
  resumableRef.current = resumable;

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
