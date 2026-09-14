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
  /** The session socket reported an error. */
  sessionError: boolean;
}

/**
 * Whether an in-flight open-thread transition is over — successfully (the
 * target thread became current), by a session error, or by abandonment (the
 * user navigated somewhere else — browser Back, a new chat, another thread
 * — before the resume landed). Mid-transition the pathname already points at
 * the target (openThread navigates in the same commit that sets the
 * transition) while currentThreadId is still the old thread or undefined:
 * that is the normal in-flight state, not abandonment.
 *
 * A resume the server refuses has no error frame of its own any more. It
 * arrives as `session.ready` naming a different thread, which the address
 * bar is then brought into line with — so it retires here as abandonment,
 * through the pathname, exactly like a user walking away.
 */
export const shouldRetireTransition = ({
  transition,
  currentThreadId,
  pathname,
  sessionError
}: RetireTransitionArgs): boolean => {
  if (!transition) return false;
  // Success: the opened thread is current. From here the guard against
  // double events is the no-op on the current thread.
  if (currentThreadId === transition.threadId) return true;
  // Failure: the session this open was riding on is gone.
  if (sessionError) return true;
  // Abandonment: the navigation this transition belongs to is gone.
  return pathname !== `/thread/${transition.threadId}`;
};
