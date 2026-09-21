import { isEqual } from 'lodash';
import { AtomEffect, DefaultValue, atom, selector } from 'recoil';

import type { ProtocolError } from './protocol';
import type { SessionDescriptor } from './transport';
import {
  IAction,
  IAsk,
  IAuthConfig,
  IChainlitConfig,
  IElementSidebarState,
  IMessageElement,
  IStep,
  ITasklistElement,
  IUser,
  ThreadHistory
} from './types';
import { groupByDate } from './utils/group';

/**
 * The last `error` message from the server, or undefined.
 *
 * Replaces the old `resume_thread_error` atom, which carried a bare string
 * for the one failure the wire could name. `error` names every one of them
 * with an `ErrorCode`, so consumers filter on the code instead of each
 * getting a channel of its own.
 */
export const protocolErrorState = atom<ProtocolError | undefined>({
  key: 'ProtocolError',
  default: undefined
});

// Which thread this page load is *asking* for. A function rather than a
// value because the host decides where the request comes from -- in the
// frontend it is the address bar, read at atom initialisation -- and this
// package knows nothing about routes. Set before RecoilRoot mounts.
//
// It has to be synchronous, and that is the whole point: `ThreadAddressSync`
// compares the URL's thread against the descriptor's on mount, before a
// single frame can arrive, and calls `clear()` when they differ. A
// descriptor that learned its thread from an effect would always differ on
// that first commit and send `session.clear` to the session the server had
// just kept.
export const sessionDescriptorSeed: {
  threadId?: () => string | undefined;
} = {};

// Nothing about a conversation is written down between page loads any more.
// The thread comes out of the address bar, which is the one place that can
// name it; the session id is minted by the server and is only ever a handle
// for HTTP routes, so there is nothing to persist and nothing a duplicated
// tab could inherit and hijack. A second tab on the same address is a
// takeover the server decides on, not a collision this side has to prevent.
const seedEffect: AtomEffect<SessionDescriptor> = ({ setSelf }) => {
  const threadId = sessionDescriptorSeed.threadId?.();
  // Absent, not present-and-undefined: the transport compares descriptors
  // by shape, and `clear()` writes the key only when it has one.
  if (threadId) setSelf({ threadId });
};

/**
 * What the socket is currently for: the thread it resumes and the profile
 * the client offers.
 *
 * One atom rather than two, because navigation moves both at once and the
 * transport reacts to the result. `clear()`, a profile hand-off and a thread
 * resume each mint a whole descriptor in a single write; as separate atoms
 * every one of those changes reached the connect effect on its own, and the
 * intermediate combinations were real states a socket got opened on.
 *
 * The two selectors below are views onto it, so the components that only
 * read one field keep doing so.
 */
export const sessionDescriptorState = atom<SessionDescriptor>({
  key: 'SessionDescriptor',
  default: {},
  effects: [seedEffect]
});

/**
 * The server's handle for this session's live objects, as announced in
 * `session.ready`.
 *
 * Deliberately outside the descriptor: it is not a request, it is an answer,
 * and nothing may connect on it. Uploads, action calls and custom-element
 * writes address the session over HTTP with it, so every one of those has to
 * survive it being `undefined` — which it is before the first `session.ready`
 * and again from the moment `clear()` gives the session up, or an early click
 * would act on the session that was just abandoned.
 */
export const sessionIdState = atom<string | undefined>({
  key: 'SessionId',
  default: undefined
});

export const chatProfileState = selector<string | undefined>({
  key: 'ChatProfile',
  get: ({ get }) => get(sessionDescriptorState).chatProfile,
  set: ({ set }, newValue) =>
    set(sessionDescriptorState, (descriptor) => ({
      ...descriptor,
      chatProfile: newValue instanceof DefaultValue ? undefined : newValue
    }))
});

export const threadIdToResumeState = selector<string | undefined>({
  key: 'ThreadIdToResume',
  get: ({ get }) => get(sessionDescriptorState).threadId,
  set: ({ set }, newValue) =>
    set(sessionDescriptorState, (descriptor) => ({
      ...descriptor,
      threadId: newValue instanceof DefaultValue ? undefined : newValue
    }))
});

export const actionState = atom<IAction[]>({
  key: 'Actions',
  default: []
});

export const messagesState = atom<IStep[]>({
  key: 'Messages',
  dangerouslyAllowMutability: true,
  default: []
});

export const loadingState = atom<boolean>({
  key: 'Loading',
  default: false
});

/**
 * Whether the composer may send. Used to be `!loading` and is not any more:
 * an application can declare a run background (`cl.run_in_background`), and
 * then the work is shown as running while the person keeps typing into it.
 * Default `true`, so a client that has heard nothing yet is a client whose
 * composer works.
 */
export const acceptingState = atom<boolean>({
  key: 'Accepting',
  default: true
});

export const askUserState = atom<IAsk | undefined>({
  key: 'AskUser',
  default: undefined
});

export const elementState = atom<IMessageElement[]>({
  key: 'DisplayElements',
  default: []
});

export const tasklistState = atom<ITasklistElement[]>({
  key: 'TasklistElements',
  default: []
});

export const firstUserInteraction = atom<string | undefined>({
  key: 'FirstUserInteraction',
  default: undefined
});

export const userState = atom<IUser | undefined | null>({
  key: 'User',
  default: undefined
});

export const configState = atom<IChainlitConfig | undefined>({
  key: 'ChainlitConfig',
  default: undefined
});

export const authState = atom<IAuthConfig | undefined>({
  key: 'AuthConfig',
  default: undefined
});

export const threadHistoryState = atom<ThreadHistory | undefined>({
  key: 'ThreadHistory',
  default: {
    threads: undefined,
    currentThreadId: undefined,
    timeGroupedThreads: undefined,
    pageInfo: undefined
  },
  effects: [
    ({ setSelf, onSet }: { setSelf: any; onSet: any }) => {
      onSet(
        (
          newValue: ThreadHistory | undefined,
          oldValue: ThreadHistory | undefined
        ) => {
          let timeGroupedThreads = newValue?.timeGroupedThreads;
          if (
            newValue?.threads &&
            !isEqual(newValue.threads, oldValue?.timeGroupedThreads)
          ) {
            timeGroupedThreads = groupByDate(newValue.threads);
          }

          setSelf({
            ...newValue,
            timeGroupedThreads
          });
        }
      );
    }
  ]
});

/**
 * The element panel, as the server holds it.
 *
 * Replaces `sideViewState`, which was one `{title, elements, key}` object —
 * one slot, and `undefined` for "closed". Closing it therefore *destroyed*
 * what was in it, nothing survived a reload, and two writers (the socket and
 * the feed's `display: 'side'` effect) fought over the same field.
 *
 * Never `undefined`: an empty panel is `{slots: [], active: null, visible:
 * false}`, so every reader is reading a state rather than testing for one.
 * The server owns it; this atom is a projection, written whole by the
 * `sidebar.state` handler and locally by `useElementSidebar`'s dispatch.
 */
export const elementSidebarState = atom<IElementSidebarState>({
  key: 'ElementSidebar',
  default: { slots: [], active: null, visible: false, rev: 0 }
});

/**
 * The thread the session is actually in, as opposed to the one it was
 * opened to resume.
 *
 * Written from `session.ready` on every connection: the server names the
 * thread on every branch, and that answer -- not anything this client
 * remembers -- is what the address bar is then brought into line with.
 */
export const currentThreadIdState = atom<string | undefined>({
  key: 'CurrentThreadId',
  default: undefined
});

/**
 * How many things the account page holds that the user has not seen.
 *
 * Pushed, never polled: the server sends `account.badge` when it has a new
 * answer and this atom is the projection. `undefined` means "the application
 * registered no badge hook, or nothing has arrived yet" and renders nothing —
 * a client-invented `0` would claim an answer the app never gave.
 *
 * Not reset by `clear()`: the count belongs to the user, not to the session,
 * and blanking it on every new chat would flicker the dot off and back on.
 */
export const accountBadgeState = atom<number | undefined>({
  key: 'AccountBadge',
  default: undefined
});
