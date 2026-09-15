import { useRecoilCallback, useRecoilValue } from 'recoil';
import { elementSidebarState } from 'src/state';
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

export { useElementSidebar };
