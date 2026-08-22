import type { IStep } from '@chainlit/react-client';

export const WAIT_DEFAULT_INTERVAL_MS = 5000;
export const WAIT_MIN_INTERVAL_MS = 2000;

/**
 * Interval between rotation texts. The server already clamps, but the client
 * defends anyway: default 5000ms, never below 2000ms.
 */
const clampWaitIntervalMs = (intervalMs?: number): number => {
  if (typeof intervalMs !== 'number' || !Number.isFinite(intervalMs)) {
    return WAIT_DEFAULT_INTERVAL_MS;
  }
  return Math.max(WAIT_MIN_INTERVAL_MS, intervalMs);
};

/**
 * Index of the next rotation text. With `loop` the list cycles; without it
 * the rotation holds on the last text (returns the same index).
 */
const nextWaitIndex = (
  current: number,
  count: number,
  loop: boolean
): number => {
  if (count <= 0) return 0;
  if (current + 1 < count) return current + 1;
  return loop ? 0 : count - 1;
};

/**
 * Last step of the conversation in document order: the deepest last
 * descendant of the last root step. Anything appended to the feed after a
 * waiting message (new message, stream start, ask, tool step) becomes the
 * last step and thereby deactivates its wait mode.
 */
const getLastStep = (messages: IStep[]): IStep | undefined => {
  const last = messages.length ? messages[messages.length - 1] : undefined;
  if (!last) return undefined;
  if (last.steps?.length) return getLastStep(last.steps);
  return last;
};

/**
 * Id of the step currently in wait mode: the conversation's last step in
 * document order, and only if it carries the transient `wait` field — which
 * also guarantees at most one message shimmers at a time. Undefined for any
 * conversation not ending in a wait message, so the value (and everything
 * derived from it, e.g. the message context) stays stable for apps that
 * never use wait messages.
 */
const getActiveWaitStepId = (messages: IStep[]): string | undefined => {
  const last = getLastStep(messages);
  return last?.wait ? last.id : undefined;
};

/**
 * A step renders in wait mode iff it carries the transient `wait` field AND
 * it is the conversation's active wait step.
 */
const isWaitActive = (step: IStep, activeWaitStepId?: string): boolean =>
  Boolean(step.wait) &&
  activeWaitStepId !== undefined &&
  step.id === activeWaitStepId;

export {
  clampWaitIntervalMs,
  getActiveWaitStepId,
  isWaitActive,
  nextWaitIndex
};
