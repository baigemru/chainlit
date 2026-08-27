import { isEqual } from 'lodash';
import { AtomEffect, DefaultValue, atom, selector } from 'recoil';
import { Socket } from 'socket.io-client';
import { v4 as uuidv4 } from 'uuid';

import { ICommand } from './types/command';
import { IMode } from './types/mode';

import {
  IAction,
  IAsk,
  IAuthConfig,
  ICallFn,
  IChainlitConfig,
  IMcp,
  IMessageElement,
  IStep,
  ITasklistElement,
  IUser,
  ThreadHistory
} from './types';
import { groupByDate } from './utils/group';
import { WavRecorder, WavStreamPlayer } from './wavtools';

export interface ISession {
  socket: Socket;
  error?: boolean;
}

export const threadIdToResumeState = atom<string | undefined>({
  key: 'ThreadIdToResume',
  default: undefined
});

export const resumeThreadErrorState = atom<string | undefined>({
  key: 'ResumeThreadErrorState',
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

export const commandsState = atom<ICommand[]>({
  key: 'Commands',
  default: []
});

export const modesState = atom<IMode[]>({
  key: 'Modes',
  default: []
});

export const tokenCountState = atom<number>({
  key: 'TokenCount',
  default: 0
});

export const loadingState = atom<boolean>({
  key: 'Loading',
  default: false
});

export const askUserState = atom<IAsk | undefined>({
  key: 'AskUser',
  default: undefined
});

export const wavRecorderState = atom({
  key: 'WavRecorder',
  dangerouslyAllowMutability: true,
  default: new WavRecorder()
});

export const wavStreamPlayerState = atom({
  key: 'WavStreamPlayer',
  dangerouslyAllowMutability: true,
  default: new WavStreamPlayer()
});

export const audioConnectionState = atom<'connecting' | 'on' | 'off'>({
  key: 'AudioConnection',
  default: 'off'
});

export const isAiSpeakingState = atom({
  key: 'isAiSpeaking',
  default: false
});

export const callFnState = atom<ICallFn | undefined>({
  key: 'CallFn',
  default: undefined
});

export const chatSettingsInputsState = atom<any>({
  key: 'ChatSettings',
  default: []
});

export const chatSettingsDefaultValueSelector = selector({
  key: 'ChatSettingsValue/Default',
  get: ({ get }) => {
    const chatSettings = get(chatSettingsInputsState);

    const collectInitialValues = (
      inputs: any[],
      acc: Record<string, any>
    ): Record<string, any> => {
      if (!Array.isArray(inputs)) {
        return acc;
      }

      inputs.forEach((input) => {
        if (!input) {
          return;
        }
        if (Array.isArray(input?.inputs) && input.inputs.length > 0) {
          // Handle tabs
          collectInitialValues(input.inputs, acc);
        } else if (input?.id !== undefined) {
          acc[input.id] = input.initial;
        }
      });

      return acc;
    };

    return collectInitialValues(chatSettings, {});
  }
});

export const chatSettingsValueState = atom<Record<string, any>>({
  key: 'ChatSettingsValue',
  default: chatSettingsDefaultValueSelector
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
  <T>(key: string, migrate?: (value: unknown) => T): AtomEffect<T> =>
  ({ setSelf, onSet }) => {
    // When the atom is first initialized, try to get its value from localStorage
    const savedValue = localStorage.getItem(key);
    if (savedValue != null) {
      try {
        const parsed = JSON.parse(savedValue);
        setSelf(migrate ? migrate(parsed) : parsed);
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

const isPlainStringRecord = (value: unknown): value is Record<string, string> =>
  !!value &&
  typeof value === 'object' &&
  !Array.isArray(value) &&
  Object.values(value as Record<string, unknown>).every(
    (v) => typeof v === 'string'
  );

const isStoredMcp = (entry: unknown): entry is IMcp => {
  if (
    !entry ||
    typeof entry !== 'object' ||
    typeof (entry as IMcp).name !== 'string' ||
    !Array.isArray((entry as IMcp).tools) ||
    typeof (entry as IMcp).status !== 'string'
  ) {
    return false;
  }

  const mcp = entry as IMcp;

  if (mcp.type !== undefined && typeof mcp.type !== 'string') {
    return false;
  }
  if (mcp.clientType !== undefined && typeof mcp.clientType !== 'string') {
    return false;
  }
  if (mcp.url !== undefined && typeof mcp.url !== 'string') {
    return false;
  }
  if (mcp.headers !== undefined && !isPlainStringRecord(mcp.headers)) {
    return false;
  }

  return true;
};

// Entries persisted before `isUserProvided` existed never had it set, but by
// construction only user-provided (SSE/streamable-http) connections ever had
// both `url` and `clientType` -- named servers never had a client-supplied
// `url`. Backfill the flag so old localStorage entries still route through
// the (validated) user-provided reconnect flow instead of being mistaken for
// a named, developer-configured server.
// Exported for testing; also usable by consumers healing state outside of
// this atom's own effect.
export const migrateStoredMcps = (value: unknown): IMcp[] => {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter(isStoredMcp)
    .map((mcp) =>
      mcp.isUserProvided === undefined && mcp.url && mcp.clientType
        ? { ...mcp, isUserProvided: true }
        : mcp
    )
    .map((mcp) =>
      // clientType:'stdio' could only have come from a pre-fix backend describing a *named*
      // server -- ConnectMCPRequest.clientType is Literal['sse','streamable-http'], so the
      // browser could never have sent it. Backfill `type` so List.tsx:135's stdio indicator
      // (which checks mcp.type only) still renders for entries stored before this release.
      // (The cast below is needed because current-schema IMcp.clientType no longer admits
      // 'stdio' -- only pre-fix localStorage entries can carry that legacy value.)
      mcp.type === undefined && (mcp.clientType as string) === 'stdio'
        ? { ...mcp, type: 'stdio' as const }
        : mcp
    );
};

export const mcpState = atom<IMcp[]>({
  key: 'Mcp',
  default: [],
  effects: [localStorageEffect<IMcp[]>('mcp_storage_key', migrateStoredMcps)]
});

export const favoriteMessagesState = atom<IStep[]>({
  key: 'favoriteMessagesState',
  default: []
});
