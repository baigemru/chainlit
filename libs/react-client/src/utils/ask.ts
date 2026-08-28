import { IAction } from '../types/action';
import { IAsk } from '../types/file';

/**
 * Drop the previous ask's own buttons from the actions list when that ask
 * stops being the current one (replaced by a foreign ask, cleared, or
 * timed out). Without this, a dead ask's buttons survive in the atom and
 * render as regular action buttons whose click 404s: the server-side
 * `remove_action` only runs after a normally-answered ask, and the dead
 * ask's server may never reach this client at all.
 *
 * `incomingStepId` guards the reconnect restore of the SAME ask: the
 * server re-emits the actions first and the ask second, so removing the
 * previous ask's keys there would erase the just-re-emitted buttons.
 *
 * Only ids listed in the previous ask's `spec.keys` are removed — regular
 * message actions are never touched. Returns the input array untouched
 * (same reference) when there is nothing to remove.
 */
export const pruneAskActions = (
  actions: IAction[],
  prevAsk: IAsk | undefined,
  incomingStepId?: string
): IAction[] => {
  if (!prevAsk) return actions;
  if (incomingStepId && prevAsk.spec.stepId === incomingStepId) {
    return actions;
  }
  const keys = prevAsk.spec.keys ?? [];
  if (!keys.length) return actions;
  const doomed = new Set(keys);
  const pruned = actions.filter((action) => !doomed.has(action.id));
  return pruned.length === actions.length ? actions : pruned;
};
