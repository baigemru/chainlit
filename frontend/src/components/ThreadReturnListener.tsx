import { shouldRetireTransition } from '@/lib/openThread';
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useRecoilState, useRecoilValue, useSetRecoilState } from 'recoil';

import {
  ErrorCode,
  currentThreadIdState,
  protocolErrorState,
  sessionIdState,
  useChatSession
} from '@chainlit/react-client';

import { useOpenThread } from '@/hooks/useOpenThread';

import {
  openThreadRequestState,
  openThreadTransitionState,
  parentThreadEntryState
} from '@/state/chat';

/**
 * The client side of returning to a parent thread. Keeps track of the
 * current chat's parent (fed by the `thread.parent` message and by resumed
 * threads), executes `thread.open` messages and the composer button's
 * requests through useOpenThread, and retires the transition state once the
 * opened thread is current (or the resume failed). Lives next to
 * ChatProfileSwitchListener, under the router; none of this exists in the
 * copilot widget, which keeps the whole feature inert there.
 */
export default function ThreadReturnListener() {
  const { session } = useChatSession();
  const location = useLocation();
  const openThread = useOpenThread();

  const sessionId = useRecoilValue(sessionIdState);
  const currentThreadId = useRecoilValue(currentThreadIdState);
  const protocolError = useRecoilValue(protocolErrorState);
  const setParentEntry = useSetRecoilState(parentThreadEntryState);
  const [transition, setTransition] = useRecoilState(openThreadTransitionState);
  const [request, setRequest] = useRecoilState(openThreadRequestState);

  // Latest values live in refs so the socket subscription below is only
  // re-registered when the socket itself changes.
  const openThreadRef = useRef(openThread);
  openThreadRef.current = openThread;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  useEffect(() => {
    const socket = session?.socket;
    if (!socket) return;

    return socket.subscribe((message) => {
      switch (message.t) {
        case 'thread.open':
          if (!message.threadId) {
            console.warn('thread.open: missing threadId, ignoring.');
            return;
          }
          openThreadRef.current(
            message.threadId,
            message.keepTranscript ?? true
          );
          return;

        // Sent during session init when the live session was spawned by a
        // profile switch; re-sent on reconnects of the same session. Scoped
        // to the session id so it dies with the session instead of leaking.
        case 'thread.parent':
          if (!message.parentThreadId) return;
          setParentEntry({
            parentThreadId: message.parentThreadId,
            forSessionId: sessionIdRef.current
          });
          return;

        // A resumed thread carries its parent itself: as a top-level field
        // in this fork, in the metadata for data layers that stash it there.
        // Always overwrite the entry — a thread without a parent must clear
        // it, so the previous chat's parent can never survive into this one.
        case 'thread.resume': {
          const { thread } = message;
          const parentThreadId =
            thread.parentThreadId ??
            (thread.metadata?.parentThreadId as string | undefined);
          setParentEntry(
            parentThreadId && typeof parentThreadId === 'string'
              ? { parentThreadId, forThreadId: thread.id }
              : undefined
          );
          return;
        }

        default:
          return;
      }
    });
  }, [session?.socket, setParentEntry]);

  // The composer's return button cannot navigate itself (it also renders in
  // the copilot widget, outside any router), so it parks a request that is
  // executed here.
  useEffect(() => {
    if (!request) return;
    setRequest(undefined);
    openThreadRef.current(request.threadId, request.keepTranscript);
  }, [request, setRequest]);

  // Retire the transition once it is over: the opened thread became current
  // (from here the guard against double events is the no-op on the current
  // thread), the resume failed, or the user abandoned it by navigating
  // somewhere else before the resume landed — otherwise the in-flight guard
  // would swallow every future open until a reload.
  useEffect(() => {
    if (
      shouldRetireTransition({
        transition,
        currentThreadId,
        pathname: location.pathname,
        resumeError: protocolError?.code === ErrorCode.THREAD_NOT_FOUND,
        sessionError: !!session?.error
      })
    ) {
      setTransition(undefined);
    }
  }, [
    transition,
    currentThreadId,
    location.pathname,
    protocolError,
    session?.error,
    setTransition
  ]);

  return null;
}
