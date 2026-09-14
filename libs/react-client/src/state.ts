import { isEqual } from 'lodash';
import { AtomEffect, DefaultValue, atom, selector } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

import type { ProtocolError } from './protocol';
import type { SessionDescriptor } from './transport';
import {
  IAction,
  IAsk,
  IAuthConfig,
  IChainlitConfig,
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

// Storage key for the persisted session id. Mutable on purpose: embedders
// that share a tab with the main app (the copilot widget) must override it
// before mounting, otherwise both clients would fight over one server
// session.
export const sessionIdStorage = { key: 'chainlit-session-id' };

// Which thread this page load is *asking* for. A function rather than a
// value because the host decides where the request comes from -- in the
// frontend it is the address bar, read at atom initialisation -- and this
// package knows nothing about routes. Set before RecoilRoot mounts, like
// `sessionIdStorage.key` above.
//
// It has to be synchronous, and that is the whole point: `AutoResumeThread`
// compares the URL's thread against the descriptor's on mount, before a
// single frame can arrive, and calls `clear()` when they differ. A
// descriptor that learned its thread from an effect would always differ on
// that first commit and send `session.clear` to the session the server had
// just kept.
export const sessionDescriptorSeed: {
  threadId?: () => string | undefined;
} = {};

// A saved session id is only reused when this page load is a plain reload
// of the same tab. A brand-new navigation — including tabs opened from this
// one via target=_blank or window.open, which inherit a copy of
// sessionStorage — must NOT adopt the id, or the new tab would silently
// hijack the original tab's server session. 'back_forward' is deliberately
// excluded too: Chromium reports it for duplicated/reopened tabs. In old
// browsers without Navigation Timing L2 this degrades to the historical
// behavior (a fresh id on every load).
const isReloadNavigation = (): boolean => {
  try {
    const nav = performance.getEntriesByType('navigation')[0] as
      | PerformanceNavigationTiming
      | undefined;
    return nav?.type === 'reload';
  } catch (_error) {
    return false;
  }
};

// Persist the session id in sessionStorage (per-tab, survives F5) so a page
// reload reconnects to the same server session and a pending ask can be
// restored. sessionStorage is deliberate: localStorage would collapse every
// tab into a single server session.
//
// Only the id. The thread is asked for by the host seed on both branches --
// storage has no say in it, because two places naming the thread is two
// answers to the same question, and the one that disagrees with the address
// bar is the one that wipes a live session.
const sessionStorageSessionIdEffect: AtomEffect<SessionDescriptor> = ({
  setSelf,
  onSet
}) => {
  const requested = () => {
    const threadId = sessionDescriptorSeed.threadId?.();
    // Absent, not present-and-undefined: the transport compares descriptors
    // by shape, and `clear()` writes the key only when it has one.
    return threadId ? { threadId } : {};
  };

  try {
    const saved = isReloadNavigation()
      ? sessionStorage.getItem(sessionIdStorage.key)
      : null;
    if (saved) {
      setSelf({ sessionId: saved, ...requested() });
    } else {
      const fresh = uuidv4();
      sessionStorage.setItem(sessionIdStorage.key, fresh);
      setSelf({ sessionId: fresh, ...requested() });
    }
  } catch (_error) {
    // Storage unavailable (sandboxed iframe, privacy mode): a fresh id, as
    // ever -- but still the thread the address bar asked for, or a reload
    // there would be read as "resume something else" and clear the session.
    setSelf({ sessionId: uuidv4(), ...requested() });
  }

  onSet((descriptor) => {
    try {
      sessionStorage.setItem(sessionIdStorage.key, descriptor.sessionId);
    } catch (_error) {
      // Ignore storage failures; the atom still holds the id.
    }
  });
};

/**
 * What the socket is currently for: the session, the thread it resumes, and
 * the profile the client offers.
 *
 * One atom rather than three, because navigation moves all of them at once
 * and the transport reacts to the result. `clear()`, a profile hand-off and
 * a thread resume each mint a whole descriptor in a single write; when they
 * were three atoms every one of those changes reached the connect effect
 * separately, and the intermediate combinations — a new session id still
 * pointing at the previous thread — were real states the socket was opened
 * on.
 *
 * The three selectors below are views onto it, so the components that only
 * read one field keep doing so.
 */
export const sessionDescriptorState = atom<SessionDescriptor>({
  key: 'SessionDescriptor',
  default: { sessionId: uuidv4() },
  effects: [sessionStorageSessionIdEffect]
});

export const sessionIdState = selector<string>({
  key: 'SessionId',
  get: ({ get }) => get(sessionDescriptorState).sessionId,
  // A reset means "give me a different session", which is a fresh uuid --
  // the DefaultValue itself never reaches the atom.
  set: ({ set }, newValue) =>
    set(sessionDescriptorState, (descriptor) => ({
      ...descriptor,
      sessionId: newValue instanceof DefaultValue ? uuidv4() : newValue
    }))
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

export const sideViewState = atom<
  { title: string; elements: IMessageElement[]; key?: string } | undefined
>({
  key: 'SideView',
  default: undefined
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
