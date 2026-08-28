import { isEqual } from 'lodash';
import { AtomEffect, DefaultValue, atom, selector } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

import type { ProtocolError } from './protocol';
import { ChainlitSocket } from './socket';
import {
  IAction,
  IAsk,
  IAuthConfig,
  IChainlitConfig,
  IMcp,
  IMessageElement,
  IStep,
  ITasklistElement,
  IUser,
  ThreadHistory
} from './types';
import { groupByDate } from './utils/group';

export interface ISession {
  socket: ChainlitSocket;
  error?: boolean;
}

export const threadIdToResumeState = atom<string | undefined>({
  key: 'ThreadIdToResume',
  default: undefined
});

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

export const chatProfileState = atom<string | undefined>({
  key: 'ChatProfile',
  default: undefined
});

// Storage key for the persisted session id. Mutable on purpose: embedders
// that share a tab with the main app (the copilot widget) must override it
// before mounting, otherwise both clients would fight over one server
// session.
export const sessionIdStorage = { key: 'chainlit-session-id' };

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
const sessionStorageSessionIdEffect: AtomEffect<string> = ({
  setSelf,
  onSet
}) => {
  try {
    const saved = isReloadNavigation()
      ? sessionStorage.getItem(sessionIdStorage.key)
      : null;
    if (saved) {
      setSelf(saved);
    } else {
      const fresh = uuidv4();
      sessionStorage.setItem(sessionIdStorage.key, fresh);
      setSelf(fresh);
    }
  } catch (_error) {
    // Storage unavailable (sandboxed iframe, privacy mode): fall back to the
    // in-memory id, i.e. the historical behavior.
  }

  onSet((newValue) => {
    // Resets never reach the atom: the sessionIdState selector converts a
    // DefaultValue into a fresh uuid before writing.
    try {
      sessionStorage.setItem(sessionIdStorage.key, newValue);
    } catch (_error) {
      // Ignore storage failures; the atom still holds the id.
    }
  });
};

const sessionIdAtom = atom<string>({
  key: 'SessionId',
  default: uuidv4(),
  effects: [sessionStorageSessionIdEffect]
});

export const sessionIdState = selector({
  key: 'SessionIdSelector',
  get: ({ get }) => get(sessionIdAtom),
  set: ({ set }, newValue) =>
    set(sessionIdAtom, newValue instanceof DefaultValue ? uuidv4() : newValue)
});

export const sessionState = atom<ISession | undefined>({
  key: 'Session',
  dangerouslyAllowMutability: true,
  default: undefined
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

export const currentThreadIdState = atom<string | undefined>({
  key: 'CurrentThreadId',
  default: undefined
});

const localStorageEffect =
  <T>(key: string): AtomEffect<T> =>
  ({ setSelf, onSet }) => {
    // When the atom is first initialized, try to get its value from localStorage
    const savedValue = localStorage.getItem(key);
    if (savedValue != null) {
      try {
        setSelf(JSON.parse(savedValue));
      } catch (error) {
        console.error(
          `Error parsing localStorage value for key "${key}":`,
          error
        );
      }
    }

    // Subscribe to state changes and update localStorage
    onSet((newValue, _, isReset) => {
      if (isReset) {
        localStorage.removeItem(key);
      } else {
        localStorage.setItem(key, JSON.stringify(newValue));
      }
    });
  };

export const mcpState = atom<IMcp[]>({
  key: 'Mcp',
  default: [],
  effects: [localStorageEffect<IMcp[]>('mcp_storage_key')]
});
