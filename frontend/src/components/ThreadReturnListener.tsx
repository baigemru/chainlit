import { shouldRetireTransition } from '@/lib/openThread';
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { useRecoilState, useRecoilValue, useSetRecoilState } from 'recoil';

import {
  IThread,
  currentThreadIdState,
  resumeThreadErrorState,
  sessionIdState,
  useChatSession
} from '@chainlit/react-client';

import { useOpenThread } from '@/hooks/useOpenThread';

import {
  openThreadRequestState,
  openThreadTransitionState,
  parentThreadEntryState
} from '@/state/chat';

interface OpenThreadPayload {
  threadId: string;
  /** Defaults to true when absent. */
  keepTranscript?: boolean;
}

interface ParentThreadPayload {
  parentThreadId: string;
}

/**
 * The client side of returning to a parent thread. Keeps track of the
 * current chat's parent (fed by the `parent_thread` session event and by
 * resumed threads), executes `open_thread` events and the composer button's
 * requests through useOpenThread, and retires the transition state once the
 * opened thread is current (or the resume failed). Lives next to
 * ChatProfileSwitchListener, under the router; none of this exists in the
 * copilot widget, which keeps the whole feature inert there. Older backends
 * never emit these events, so subscribing to them changes nothing.
 */
export default function ThreadReturnListener() {
  const { session } = useChatSession();
  const location = useLocation();
  const openThread = useOpenThread();

  const sessionId = useRecoilValue(sessionIdState);
  const currentThreadId = useRecoilValue(currentThreadIdState);
  const resumeThreadError = useRecoilValue(resumeThreadErrorState);
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

    const onOpenThread = (payload: OpenThreadPayload) => {
      if (!payload?.threadId || typeof payload.threadId !== 'string') {
        console.warn('open_thread: missing threadId, ignoring.');
        return;
      }
      openThreadRef.current(payload.threadId, payload.keepTranscript ?? true);
    };

    // Sent during session init when the live session was spawned by a
    // profile switch; re-sent on reconnects of the same session. Scoped to
    // the session id so it dies with the session instead of leaking.
    const onParentThread = (payload: ParentThreadPayload) => {
      if (
        !payload?.parentThreadId ||
        typeof payload.parentThreadId !== 'string'
      ) {
        return;
      }
      setParentEntry({
        parentThreadId: payload.parentThreadId,
        forSessionId: sessionIdRef.current
      });
    };

    // A resumed thread carries its parent itself: as a top-level field in
    // this fork, in the metadata for data layers that stash it there. Always
    // overwrite the entry — a thread without a parent must clear it, so the
    // previous chat's parent can never survive into this one.
    const onResumeThread = (thread: IThread) => {
      const parentThreadId =
        thread?.parentThreadId ?? thread?.metadata?.parentThreadId;
      setParentEntry(
        parentThreadId && typeof parentThreadId === 'string'
          ? { parentThreadId, forThreadId: thread.id }
          : undefined
      );
    };

    socket.on('open_thread', onOpenThread);
    socket.on('parent_thread', onParentThread);
    socket.on('resume_thread', onResumeThread);
    return () => {
      socket.off('open_thread', onOpenThread);
      socket.off('parent_thread', onParentThread);
      socket.off('resume_thread', onResumeThread);
    };
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
        resumeError: !!resumeThreadError,
        sessionError: !!session?.error
      })
    ) {
      setTransition(undefined);
    }
  }, [
    transition,
    currentThreadId,
    location.pathname,
    resumeThreadError,
    session?.error,
    setTransition
  ]);

  return null;
}
