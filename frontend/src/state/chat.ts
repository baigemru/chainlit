import { atom } from 'recoil';

import { IStep } from 'client-types/*';

export interface IAttachment {
  id: string;
  serverId?: string;
  name: string;
  size: number;
  type: string;
  uploadProgress?: number;
  uploaded?: boolean;
  cancel?: () => void;
  remove?: () => void;
  file?: File;
}

export const attachmentsState = atom<IAttachment[]>({
  key: 'Attachments',
  default: []
});

export interface IChatBoundary {
  /** Id of the last root message of the chat that ended here. */
  afterMessageId: string;
  /** Profile the new chat started on, used to label the divider. */
  profile: string;
}

// Where a `set_chat_profile(keep_transcript=True)` switch ended one chat and
// started the next, so the transcript can be split with a divider. Client-side
// only: a reload or a thread opened from the history drops these, and an id
// matching no message simply draws nothing.
export const chatBoundariesState = atom<IChatBoundary[]>({
  key: 'ChatBoundaries',
  default: []
});

/**
 * The transcript kept on screen by one `open_thread(keepTranscript)` return:
 * everything that was visible when the return happened, with the dividers it
 * carried. Rendered above the live chat, ended by a return divider whose
 * collapse button hides the excursion's own (last) segment.
 */
export interface IKeptExcursion {
  /** Client-generated id: keys the return divider and its collapsed state. */
  id: string;
  /** Root messages that were on screen, streaming flags frozen. */
  messages: IStep[];
  /** The `chatBoundariesState` dividers that were on screen with them. */
  boundaries: IChatBoundary[];
}

// Transcripts kept by returns to a parent thread, oldest first. Client-side
// only, same lifetime as chatBoundariesState: a reload or a plain open from
// the history drops them.
export const keptExcursionsState = atom<IKeptExcursion[]>({
  key: 'KeptExcursions',
  default: []
});

// Excursion ids whose last segment (the child-chat messages) is collapsed
// into a compact strip. Pure client state, never persisted.
export const collapsedExcursionsState = atom<Record<string, boolean>>({
  key: 'CollapsedExcursions',
  default: {}
});

/**
 * Parent of the chat currently on screen. Scoped to the session or thread it
 * was learned for, so a stale parent can never leak into an unrelated chat:
 * `clear()` resets both the session id and the current thread id, which
 * invalidates the entry without anyone having to clean it up.
 */
export interface IParentThreadEntry {
  parentThreadId: string;
  /** Set when the parent came from the live session (`parent_thread`). */
  forSessionId?: string;
  /** Set when the parent came from a resumed thread's metadata. */
  forThreadId?: string;
}

export const parentThreadEntryState = atom<IParentThreadEntry | undefined>({
  key: 'ParentThreadEntry',
  default: undefined
});

/** An `open_thread` transition currently in flight. */
export interface IOpenThreadTransition {
  threadId: string;
  keepTranscript: boolean;
}

// Set when openThread hands over to the regular resume path, cleared when the
// thread becomes current (or the resume fails). While set, /thread/:id keeps
// the chat mounted so the kept transcript never yields to a loader, and any
// further open_thread events are ignored (one transition at a time).
export const openThreadTransitionState = atom<
  IOpenThreadTransition | undefined
>({
  key: 'OpenThreadTransition',
  default: undefined
});

// A click on the composer's return button. The composer renders inside the
// copilot widget too, where there is no router, so instead of navigating it
// parks the request here for ThreadReturnListener (app only) to execute.
// Every click writes a fresh object, so repeated identical requests still
// re-trigger the consuming effect.
export const openThreadRequestState = atom<IOpenThreadTransition | undefined>({
  key: 'OpenThreadRequest',
  default: undefined
});
