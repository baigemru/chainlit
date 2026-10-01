import { useEffect, useMemo } from 'react';
import { useRecoilCallback, useRecoilState, useRecoilValue } from 'recoil';
import {
  ElementSidebarSeen,
  elementSidebarSeenState,
  elementSidebarState,
  sessionIdState
} from 'src/state';
import { IElementSidebarState, IMessageElement } from 'src/types';

import { useChatTransport } from './context';

/**
 * The element panel, and the five things a user can do to it.
 *
 * Named for the panel it drives, not for "sidebar": `@/components/ui/sidebar`
 * exports a `useSidebar` for the thread-history panel on the *left*, and the
 * two are unrelated.
 *
 * Deliberately a thin client. The server owns the model and its frame is
 * idempotent, so this applies the operation locally — every time, so the UI
 * answers the click on the same frame — and sends `sidebar.user`. What comes
 * back on a structural operation overwrites the guess; nothing here has to
 * agree with the server's reducer, and there is no second implementation of
 * one to keep in step.
 */

export type SidebarOperation =
  | { op: 'hide' }
  | { op: 'show' }
  | { op: 'activate'; slot: string }
  | { op: 'close'; slot: string }
  | { op: 'preview'; element: IMessageElement };

/** The slot a click in the feed lands in. Named the same on both sides. */
export const PREVIEW_SLOT = 'preview';

/**
 * Apply one operation to the panel.
 *
 * Pure, exported and tested on its own: it is the only place the client
 * decides anything about the panel, and every component goes through it.
 */
export const applySidebarOp = (
  state: IElementSidebarState,
  operation: SidebarOperation
): IElementSidebarState => {
  switch (operation.op) {
    case 'hide':
      return { ...state, visible: false };
    case 'show':
      return { ...state, visible: true };
    case 'activate':
      return state.slots.some((slot) => slot.id === operation.slot)
        ? { ...state, active: operation.slot }
        : state;
    case 'close': {
      const index = state.slots.findIndex((slot) => slot.id === operation.slot);
      const target = state.slots[index];
      if (!target || !target.closable) return state;
      const slots = state.slots.filter((slot) => slot.id !== operation.slot);
      return {
        ...state,
        slots,
        // The neighbour the server would pick, so the optimistic frame and
        // the next one it sends agree: the previous tab, or the one that
        // slid into this index. Never the first, and never a tab that is
        // gone; never on screen with nothing in it either.
        active:
          state.active === operation.slot
            ? ((index > 0 ? slots[index - 1]?.id : slots[0]?.id) ?? null)
            : state.active,
        visible: slots.length > 0 && state.visible
      };
    }
    case 'preview': {
      const slot = {
        id: PREVIEW_SLOT,
        title: operation.element.name,
        elements: [operation.element],
        closable: true,
        canvas: false
      };
      const slots = state.slots.some((existing) => existing.id === PREVIEW_SLOT)
        ? state.slots.map((existing) =>
            existing.id === PREVIEW_SLOT ? slot : existing
          )
        : [...state.slots, slot];
      // A click in the feed is a request to *see* something, so it opens the
      // panel as well as filling it.
      return { ...state, slots, active: PREVIEW_SLOT, visible: true };
    }
  }
};

/** What the atom holds before a server has said anything. */
const EMPTY: IElementSidebarState = {
  slots: [],
  active: null,
  visible: false,
  rev: 0
};

const useElementSidebar = () => {
  const state = useRecoilValue(elementSidebarState);
  const transport = useChatTransport();

  /**
   * Do it here, and tell the server which state it was done against.
   *
   * `rev` is read out of the atom inside the updater rather than off this
   * render, because a click can land in the same tick as an incoming frame
   * and a stale quote would make the server answer an operation that needed
   * no answer.
   *
   * `local` is for a reader that is not the live conversation — a shared or
   * read-only thread. Its elements come from a REST record of a thread the
   * session has never been in, so the server would refuse a `preview` and
   * the refusal, a full state, would wipe what the click just opened.
   *
   * It is a hole in the model and is spelled out rather than hidden: the
   * atom it writes belongs to the **live session**, which is the only panel
   * there is, and the server's copy of that panel never learns of this
   * preview. So the next `sidebar.state` the live conversation sends
   * replaces it, without warning. Accepted here because a read-only view has
   * no session of its own to hold a panel for; the fix, if the seam ever
   * costs anything, is a second atom for read-only viewing rather than a
   * flag on the shared one's dispatcher.
   */
  const dispatch = useRecoilCallback(
    ({ set, snapshot }) =>
      (operation: SidebarOperation, { local }: { local?: boolean } = {}) => {
        const previous =
          snapshot.getLoadable(elementSidebarState).valueMaybe() ?? EMPTY;
        if (!local) send(transport, operation, previous.rev);
        set(elementSidebarState, applySidebarOp(previous, operation));
      },
    [transport]
  );

  return { state, dispatch };
};

/**
 * The tab a frame puts in front of the user — the same fallback the panel
 * draws, so "active" means one thing to the strip and to the dot beside it.
 */
const activeSlot = (state: IElementSidebarState): string | undefined =>
  state.active ?? state.slots[0]?.id;

/**
 * What a slot holds, as one comparable string.
 *
 * By content, not by reference: a reconnect replays the whole panel as fresh
 * `element.upsert`s, so every element object is new while not one of them
 * changed, and an identity check would light up every inactive tab after a
 * dropped connection.
 */
const signature = (slot: IElementSidebarState['slots'][number]): string =>
  JSON.stringify(slot.elements);

/**
 * Record what the user has now seen.
 *
 * The active slot is seen as it is. An inactive one keeps whatever it held
 * when it was last seen; one that *arrives* behind another tab is recorded
 * as never seen. A new session — and the first slots of an empty panel,
 * which on a reload is the whole replayed panel at once — is a baseline:
 * nothing in it is news to anybody.
 *
 * Returns `seen` itself when nothing moved, so the effect that calls it
 * settles instead of writing the atom on every render.
 */
export const noteSeen = (
  seen: ElementSidebarSeen,
  state: IElementSidebarState,
  session: string | undefined
): ElementSidebarSeen => {
  const active = activeSlot(state);
  const baseline =
    seen.session !== session || Object.keys(seen.slots).length === 0;
  const slots: Record<string, string> = {};
  for (const slot of state.slots) {
    slots[slot.id] =
      baseline || slot.id === active
        ? signature(slot)
        : // `''` never equals a signature, which is always a JSON array.
          (seen.slots[slot.id] ?? '');
  }
  const unchanged =
    seen.session === session &&
    Object.keys(slots).length === Object.keys(seen.slots).length &&
    Object.entries(slots).every(([id, sig]) => seen.slots[id] === sig);
  return unchanged ? seen : { session, slots };
};

/**
 * The inactive slots whose contents changed since the user last saw them.
 * The active one never is: being looked at is what clears it.
 */
export const freshSlots = (
  seen: ElementSidebarSeen,
  state: IElementSidebarState,
  session: string | undefined
): ReadonlySet<string> => {
  // A record from another session describes another panel; until the
  // effect has replaced it, it says nothing about this one.
  if (seen.session !== session) return new Set();
  const active = activeSlot(state);
  return new Set(
    state.slots
      .filter(
        (slot) =>
          slot.id !== active &&
          slot.id in seen.slots &&
          seen.slots[slot.id] !== signature(slot)
      )
      .map((slot) => slot.id)
  );
};

/**
 * The tabs to mark as "changed while you were elsewhere".
 *
 * Client-side by design: the server already says everything this needs —
 * every refill of a slot is a `sidebar.state` naming its elements, sent
 * after their upserts — and who has looked at what is not the server's
 * business. Call it from one place, the panel; it writes the record.
 */
const useFreshSlots = (): ReadonlySet<string> => {
  const state = useRecoilValue(elementSidebarState);
  const session = useRecoilValue(sessionIdState);
  const [seen, setSeen] = useRecoilState(elementSidebarSeenState);

  useEffect(() => {
    setSeen((previous) => noteSeen(previous, state, session));
  }, [state, session, setSeen]);

  return useMemo(
    () => freshSlots(seen, state, session),
    [seen, state, session]
  );
};

const send = (
  transport: ReturnType<typeof useChatTransport>,
  operation: SidebarOperation,
  rev: number
): void => {
  switch (operation.op) {
    case 'preview':
      transport.send({
        t: 'sidebar.user',
        op: 'preview',
        elementId: operation.element.id,
        rev
      });
      return;
    case 'activate':
    case 'close':
      transport.send({
        t: 'sidebar.user',
        op: operation.op,
        slot: operation.slot,
        rev
      });
      return;
    default:
      transport.send({ t: 'sidebar.user', op: operation.op, rev });
  }
};

export { useElementSidebar, useFreshSlots };
