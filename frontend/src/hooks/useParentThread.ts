import { useCallback } from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';

import {
  currentThreadIdState,
  sessionIdState,
  useConfig
} from '@chainlit/react-client';

import {
  collapsedExcursionsState,
  keptExcursionsState,
  parentThreadEntryState
} from '@/state/chat';

/**
 * Parent of the chat currently on screen, or undefined when it has none.
 * The stored entry is scoped to the session or thread it was learned for, so
 * anything that starts another chat (clear() resets both scopes) hides the
 * parent without explicit cleanup — a stale parent never leaks into an
 * unrelated chat.
 *
 * `ui.show_parent_thread_button` is read here and nowhere else. Both display
 * sites ask this hook — the return button itself and the composer's "is the
 * left slot empty" — and a second copy of the condition could disagree with
 * this one into a pill holding neither a button nor the chevron that stands in
 * for it. The mechanism is untouched by the flag: a server-sent `thread.open`
 * still returns a user to the parent in a deployment that hides the button.
 */
export const useParentThreadId = (): string | undefined => {
  const { config } = useConfig();
  const entry = useRecoilValue(parentThreadEntryState);
  const sessionId = useRecoilValue(sessionIdState);
  const currentThreadId = useRecoilValue(currentThreadIdState);

  if (!config?.ui?.show_parent_thread_button) return undefined;
  if (!entry) return undefined;
  if (entry.forSessionId && entry.forSessionId === sessionId) {
    return entry.parentThreadId;
  }
  if (entry.forThreadId && entry.forThreadId === currentThreadId) {
    return entry.parentThreadId;
  }
  return undefined;
};

/**
 * Drops the transcripts kept by returns to a parent thread. Must accompany
 * every action that intentionally blanks the screen (new chat, manual
 * profile change, opening another thread from the history): the excursions
 * are not id-matched against anything, so unlike stale boundaries they would
 * keep rendering above the fresh chat.
 */
export const useResetKeptTranscript = () => {
  const setKeptExcursions = useSetRecoilState(keptExcursionsState);
  const setCollapsedExcursions = useSetRecoilState(collapsedExcursionsState);

  return useCallback(() => {
    setKeptExcursions([]);
    setCollapsedExcursions({});
  }, [setKeptExcursions, setCollapsedExcursions]);
};
