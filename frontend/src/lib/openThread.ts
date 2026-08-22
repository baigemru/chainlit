import type { IOpenThreadTransition } from '@/state/chat';

/**
 * Probes whether a thread can be opened by the current user. Deliberately a
 * raw fetch instead of the shared api client: the client funnels every 401
 * into the global on401 handler, which hard-redirects to /login — but here a
 * 401 just means "not this user's thread" and must leave the current chat
 * (and the kept transcript) intact, with a single local toast. Credentials
 * are included exactly as APIBase.fetch would.
 */
export const isThreadAvailable = async (url: string): Promise<boolean> => {
  try {
    const response = await fetch(url, { credentials: 'include' });
    return response.ok;
  } catch {
    return false;
  }
};

export interface RetireTransitionArgs {
  transition: IOpenThreadTransition | undefined;
  /** Thread the chat has settled on, if any. */
  currentThreadId: string | undefined;
  /** Current router pathname. */
  pathname: string;
  /** A resume error was reported. */
  resumeError: boolean;
  /** The session socket reported an error. */
  sessionError: boolean;
}

/**
 * Whether an in-flight open-thread transition is over — successfully (the
 * target thread became current), by failure (resume or session error), or by
 * abandonment (the user navigated somewhere else — browser Back, a new chat,
 * another thread — before the resume landed). Mid-transition the pathname
 * already points at the target (openThread navigates in the same commit that
 * sets the transition) while currentThreadId is still the old thread or
 * undefined: that is the normal in-flight state, not abandonment.
 */
export const shouldRetireTransition = ({
  transition,
  currentThreadId,
  pathname,
  resumeError,
  sessionError
}: RetireTransitionArgs): boolean => {
  if (!transition) return false;
  // Success: the opened thread is current. From here the guard against
  // double events is the no-op on the current thread.
  if (currentThreadId === transition.threadId) return true;
  // Failure: AutoResumeThread is routing back home.
  if (resumeError || sessionError) return true;
  // Abandonment: the navigation this transition belongs to is gone.
  return pathname !== `/thread/${transition.threadId}`;
};
