import { debounce } from 'lodash';
import { useCallback, useContext, useEffect, useRef } from 'react';
import {
  useRecoilCallback,
  useRecoilState,
  useRecoilValue,
  useResetRecoilState,
  useSetRecoilState
} from 'recoil';
import io from 'socket.io-client';
import { toast } from 'sonner';
import {
  actionState,
  askUserState,
  audioConnectionState,
  callFnState,
  chatProfileState,
  chatSettingsInputsState,
  chatSettingsValueState,
  commandsState,
  configState,
  currentThreadIdState,
  elementState,
  favoriteMessagesState,
  firstUserInteraction,
  isAiSpeakingState,
  loadingState,
  mcpState,
  messagesState,
  modesState,
  resumeThreadErrorState,
  sessionIdState,
  sessionIdStorage,
  sessionState,
  sideViewState,
  tasklistState,
  threadIdToResumeState,
  tokenCountState,
  wavRecorderState,
  wavStreamPlayerState
} from 'src/state';
import {
  IAction,
  IAskElementResponse,
  ICommand,
  IElement,
  IFileRef,
  IMessageElement,
  IMode,
  IStep,
  ITasklistElement,
  IThread
} from 'src/types';
import { pruneAskActions } from 'src/utils/ask';
import {
  addMessage,
  deleteMessageById,
  stampChatProfile,
  updateMessageById,
  updateMessageContentById
} from 'src/utils/message';

import { OutputAudioChunk } from './types/audio';

import { ChainlitContext } from './context';
import type { IToken } from './useChatData';

// True once any connection succeeded in this page's lifetime. Reported to
// the server on connection_successful so it can distinguish a reconnect of
// a loaded page (UI state intact) from a fresh page load that needs a full
// restore of a pending ask's transcript/actions/element.
let pageHasEstablishedConnection = false;

// For embedders that unmount and remount the whole widget (copilot): the
// remounted UI starts empty, so the next connect must be treated as a
// fresh load again or the server would skip the full restore.
const resetPageConnectionFlag = () => {
  pageHasEstablishedConnection = false;
};
export { resetPageConnectionFlag };

const useChatSession = () => {
  const client = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const resetSessionId = useResetRecoilState(sessionIdState);
  // One-shot guard: a persisted session id the server refuses gets replaced
  // once; a second refusal in a row surfaces as an error instead of looping.
  const authFailureHandledRef = useRef(false);

  const [session, setSession] = useRecoilState(sessionState);
  const setIsAiSpeaking = useSetRecoilState(isAiSpeakingState);
  const setAudioConnection = useSetRecoilState(audioConnectionState);
  const resetChatSettingsValue = useResetRecoilState(chatSettingsValueState);
  const setChatSettingsValue = useSetRecoilState(chatSettingsValueState);
  const setFirstUserInteraction = useSetRecoilState(firstUserInteraction);
  const setLoading = useSetRecoilState(loadingState);
  const setMcps = useSetRecoilState(mcpState);
  const wavStreamPlayer = useRecoilValue(wavStreamPlayerState);
  const wavRecorder = useRecoilValue(wavRecorderState);
  const setMessages = useSetRecoilState(messagesState);
  const setAskUser = useSetRecoilState(askUserState);
  const setCallFn = useSetRecoilState(callFnState);
  const setCommands = useSetRecoilState(commandsState);
  const setModes = useSetRecoilState(modesState);
  const setSideView = useSetRecoilState(sideViewState);
  const setElements = useSetRecoilState(elementState);
  const setTasklists = useSetRecoilState(tasklistState);
  const setActions = useSetRecoilState(actionState);
  const setChatSettingsInputs = useSetRecoilState(chatSettingsInputsState);
  const setTokenCount = useSetRecoilState(tokenCountState);
  const [chatProfile, setChatProfile] = useRecoilState(chatProfileState);
  // The socket handlers below are registered once per connect and would
  // otherwise close over the profile that was active at that moment; the
  // ref always carries the current one, so every incoming message is
  // stamped with the profile it is actually generated under.
  const chatProfileRef = useRef(chatProfile);
  chatProfileRef.current = chatProfile;
  // Hot-swap flag, latched. It cannot be read straight from the config:
  // useConfig blanks the config on every profile change to re-fetch it for
  // the new profile, so during the switch itself the flag would read
  // undefined, the dep below would fall back to chatProfile and the socket
  // would reconnect — exactly what the feature exists to avoid. The flag is
  // server-side and profile-independent, so latching it is correct.
  const config = useRecoilValue(configState);
  const hotSwapRef = useRef(false);
  if (config?.features?.hot_swap_chat_profile) hotSwapRef.current = true;
  const hotSwap = hotSwapRef.current;
  const idToResume = useRecoilValue(threadIdToResumeState);
  const setThreadResumeError = useSetRecoilState(resumeThreadErrorState);
  const setFavoriteMessages = useSetRecoilState(favoriteMessagesState);

  const [currentThreadId, setCurrentThreadId] =
    useRecoilState(currentThreadIdState);

  // A dead ask's buttons must not outlive it in the actions atom (they
  // would render as regular action buttons whose click 404s). Called by
  // the handlers that end or replace the current ask; the incoming step
  // id (when there is one) protects the reconnect re-emit of the SAME
  // ask from erasing its own just-restored buttons.
  const pruneStaleAskActions = useRecoilCallback(
    ({ set, snapshot }) =>
      (incomingStepId?: string) => {
        const prevAsk = snapshot.getLoadable(askUserState).valueMaybe();
        set(actionState, (old) =>
          pruneAskActions(old, prevAsk, incomingStepId)
        );
      },
    []
  );

  // Use currentThreadId as thread id in websocket header
  useEffect(() => {
    if (session?.socket) {
      session.socket.auth['threadId'] = currentThreadId || '';
    }
  }, [currentThreadId]);

  const _connect = useCallback(
    async ({
      transports,
      userEnv
    }: {
      transports?: string[];
      userEnv: Record<string, string>;
    }) => {
      const { protocol, host, pathname } = new URL(client.httpEndpoint);
      const uri = `${protocol}//${host}`;
      const path =
        pathname && pathname !== '/'
          ? `${pathname}/ws/socket.io`
          : '/ws/socket.io';

      try {
        await client.stickyCookie(sessionId);
      } catch (err) {
        console.error(`Failed to set sticky session cookie: ${err}`);
      }

      const socket = io(uri, {
        path,
        withCredentials: true,
        transports,
        auth: {
          clientType: client.type,
          sessionId,
          threadId: idToResume || '',
          userEnv: JSON.stringify(userEnv),
          chatProfile: chatProfileRef.current
            ? encodeURIComponent(chatProfileRef.current)
            : '',
          // True only on the very first connect after a full page load: the
          // server restores the old session then only to rescue a live
          // pending ask; otherwise a reload means a fresh chat. Mutated to
          // false after the first successful connect so automatic transport
          // reconnects restore the session unconditionally.
          pageLoad: !pageHasEstablishedConnection
        }
      });
      if (
        typeof window !== 'undefined' &&
        (window as any).Cypress &&
        client.type !== 'copilot'
      ) {
        // Exposed for e2e tests to simulate transport drops. Only under
        // Cypress, and never for the copilot widget: a handle on the
        // user's socket must not leak to page scripts in production.
        (window as any).__chainlitSocket = socket;
      }

      setSession((old) => {
        old?.socket?.removeAllListeners();
        old?.socket?.close();
        return {
          socket
        };
      });

      socket.on('connect', () => {
        socket.emit('connection_successful');
        pageHasEstablishedConnection = true;
        (socket.auth as Record<string, unknown>)['pageLoad'] = false;
        authFailureHandledRef.current = false;
        setSession((s) => ({ ...s!, error: false }));
        socket.emit('fetch_favorites');
        setMcps((prev) =>
          prev.map((mcp) => {
            let promise;
            if (mcp.isUserProvided && mcp.url && mcp.clientType) {
              // User-provided MCP (SSE or streamable-http)
              promise = client.connectUserMcp(
                sessionId,
                mcp.name,
                mcp.clientType,
                mcp.url,
                mcp.headers
              );
            } else {
              // Named (developer-configured) MCP
              promise = client.connectMcp(sessionId, mcp.name);
            }
            promise
              .then(async ({ success, mcp }) => {
                setMcps((prev) =>
                  prev.map((existingMcp) => {
                    if (existingMcp.name === mcp.name) {
                      return {
                        ...existingMcp,
                        status: success ? 'connected' : 'failed',
                        tools: mcp ? mcp.tools : existingMcp.tools
                      };
                    }
                    return existingMcp;
                  })
                );
              })
              .catch(() => {
                setMcps((prev) =>
                  prev.map((existingMcp) => {
                    if (existingMcp.name === mcp.name) {
                      return {
                        ...existingMcp,
                        status: 'failed'
                      };
                    }
                    return existingMcp;
                  })
                );
              });
            return { ...mcp, status: 'connecting' };
          })
        );
      });

      socket.on('connect_error', (err) => {
        if (
          err?.message === 'session authorization failed' &&
          !authFailureHandledRef.current
        ) {
          // The persisted session id belongs to a session this user may not
          // claim (e.g. someone else logged in within this tab). Mint a
          // fresh id instead of retrying against the same refusal forever.
          // Once only: a refusal for another reason (e.g. a foreign thread
          // id) would repeat with the new id and must surface as an error.
          authFailureHandledRef.current = true;
          resetSessionId();
          return;
        }
        setSession((s) => ({ ...s!, error: true }));
      });

      socket.on('task_start', () => {
        setLoading(true);
      });

      socket.on('task_end', () => {
        setLoading(false);
      });

      socket.on('reload', () => {
        socket.emit('clear_session');
        try {
          // The server asked for a clean restart (dev hot-reload): drop the
          // persisted id so the reloaded page cannot race clear_session and
          // resurrect the session it was told to leave.
          sessionStorage.removeItem(sessionIdStorage.key);
        } catch (_error) {
          // Storage unavailable — the reload proceeds regardless.
        }
        window.location.reload();
      });

      socket.on('audio_connection', async (state: 'on' | 'off') => {
        if (state === 'on') {
          let isFirstChunk = true;
          const startTime = Date.now();
          const mimeType = 'pcm16';
          try {
            await wavRecorder.begin();
            await wavStreamPlayer.connect();
            await wavRecorder.record(async (data) => {
              const elapsedTime = Date.now() - startTime;
              socket.emit('audio_chunk', {
                isStart: isFirstChunk,
                mimeType,
                elapsedTime,
                data: data.mono
              });
              isFirstChunk = false;
            });
            wavStreamPlayer.onStop = () => setIsAiSpeaking(false);
          } catch {
            try {
              await wavRecorder.end();
            } catch {
              // ignored
            }
            await wavStreamPlayer.interrupt();
            socket.emit('audio_end');
            setAudioConnection('off');
            return;
          }
        } else {
          await wavRecorder.end();
          await wavStreamPlayer.interrupt();
        }
        setAudioConnection(state);
      });

      socket.on('audio_chunk', (chunk: OutputAudioChunk) => {
        wavStreamPlayer.add16BitPCM(chunk.data, chunk.track);
        setIsAiSpeaking(true);
      });

      socket.on('audio_interrupt', () => {
        wavStreamPlayer.interrupt();
      });

      socket.on('resume_thread', (thread: IThread) => {
        const isReadOnlyView = Boolean(
          (thread as any)?.metadata?.viewer_read_only
        );
        if (!isReadOnlyView && idToResume && thread.id !== idToResume) {
          window.location.href = `/thread/${thread.id}`;
        }
        if (!isReadOnlyView && idToResume) {
          setCurrentThreadId(thread.id);
        }
        let messages: IStep[] = [];
        for (const step of thread.steps) {
          messages = addMessage(messages, step);
        }
        if (thread.metadata?.chat_profile) {
          setChatProfile(thread.metadata?.chat_profile);
        }
        if (thread.metadata?.chat_settings) {
          setChatSettingsValue(thread.metadata?.chat_settings);
        }
        setMessages(messages);
        const elements = thread.elements || [];
        setTasklists(
          (elements as ITasklistElement[]).filter((e) => e.type === 'tasklist')
        );
        setElements(
          (elements as IMessageElement[]).filter(
            (e) => ['avatar', 'tasklist'].indexOf(e.type) === -1
          )
        );
      });

      socket.on('resume_thread_error', (error?: string) => {
        setThreadResumeError(error);
      });

      socket.on('new_message', (message: IStep) => {
        setMessages((oldMessages) =>
          // For an already known id addMessage merges fields into the stored
          // step; `wait` is transient, so the explicit (possibly undefined)
          // value overwrites any stored one instead of surviving the merge.
          addMessage(oldMessages, {
            ...stampChatProfile(message, chatProfileRef.current),
            wait: message.wait
          })
        );
      });

      socket.on(
        'first_interaction',
        (event: { interaction: string; thread_id: string }) => {
          setFirstUserInteraction(event.interaction);
          setCurrentThreadId(event.thread_id);
        }
      );

      socket.on('update_message', (message: IStep) => {
        setMessages((oldMessages) =>
          updateMessageById(oldMessages, message.id, {
            // updateMessageById merges fields into the stored step; `wait` is
            // transient and an update without it must end wait mode, so the
            // explicit (possibly undefined) value overwrites any stored one.
            ...stampChatProfile(message, chatProfileRef.current),
            wait: message.wait
          })
        );
      });

      socket.on('delete_message', (message: IStep) => {
        setMessages((oldMessages) =>
          deleteMessageById(oldMessages, message.id)
        );
      });

      socket.on('stream_start', (message: IStep) => {
        setMessages((oldMessages) =>
          // Same as new_message: a stream_start for an id that was in wait
          // mode must clear the stored `wait`, or the rotation text would
          // hide the streamed tokens.
          addMessage(oldMessages, {
            ...stampChatProfile(message, chatProfileRef.current),
            wait: message.wait
          })
        );
      });

      socket.on(
        'stream_token',
        ({ id, token, isSequence, isInput }: IToken) => {
          setMessages((oldMessages) =>
            updateMessageContentById(
              oldMessages,
              id,
              token,
              isSequence,
              isInput
            )
          );
        }
      );

      socket.on('ask', ({ msg, spec }, callback) => {
        const reply = (
          payload: IStep | IFileRef[] | IAction | IAskElementResponse
        ) => {
          // A plain event rather than the socket.io ack: plain emits are
          // buffered while the transport is down and redelivered after
          // reconnect, so a click during a network blip is not lost.
          socket.emit('ask_reply', { stepId: spec.step_id, value: payload });
          if (typeof callback === 'function') {
            // Legacy ack path, kept for an older backend using sio.call.
            callback(payload);
          }
          setAskUser((prev) =>
            prev && prev.spec.step_id === spec.step_id
              ? { ...prev, awaitingReply: true }
              : prev
          );
        };
        // A foreign ask replacing the previous one orphans that ask's
        // buttons — drop them. The step_id guard inside makes the SAME
        // ask's re-emit (reconnect restore) a no-op here.
        pruneStaleAskActions(spec.step_id);
        // A re-emitted ask (reconnect restore) simply rebinds the form to
        // the live socket; addMessage upserts the message by id.
        setAskUser({ spec, callback: reply, parentId: msg.parentId });
        setMessages((oldMessages) =>
          addMessage(oldMessages, stampChatProfile(msg, chatProfileRef.current))
        );

        setLoading(false);
      });

      // The only writer of the profile atom on the hot-swap path. sync=true
      // is the post-reconnect resync ("adopt this value"), sync=false a real
      // switch; both land the same way here, the flag exists for clarity and
      // for anything that later needs to tell them apart.
      socket.on('chat_profile_changed', (data) => {
        if (!data?.chatProfile) return;
        setChatProfile(data.chatProfile);
        // sync=true is the post-reconnect resync ("adopt this value"), so
        // nothing else changed. sync=false is a real switch, and the server
        // cleared session.chat_settings in the same step — mirror it, or the
        // settings modal keeps offering the previous profile's form and
        // values, which no longer exist server-side.
        if (data.sync === false) {
          setChatSettingsInputs([]);
          resetChatSettingsValue();
        }
      });

      socket.on('ask_timeout', () => {
        pruneStaleAskActions();
        setAskUser(undefined);
        setLoading(false);
      });

      socket.on('clear_ask', () => {
        pruneStaleAskActions();
        setAskUser(undefined);
      });

      socket.on('call_fn', ({ name, args }, callback) => {
        setCallFn({ name, args, callback });
      });

      socket.on('clear_call_fn', () => {
        setCallFn(undefined);
      });

      socket.on('call_fn_timeout', () => {
        setCallFn(undefined);
      });

      socket.on('chat_settings', (inputs: any) => {
        setChatSettingsInputs(inputs);
        resetChatSettingsValue();
      });

      socket.on('set_commands', (commands: ICommand[]) => {
        setCommands(commands);
      });

      socket.on('set_modes', (modes: IMode[]) => {
        setModes(modes);
      });

      socket.on('set_favorites', (steps: IStep[]) => {
        setFavoriteMessages(steps);
      });

      socket.on('set_sidebar_title', (title: string) => {
        setSideView((prev) => {
          if (prev?.title === title) return prev;
          return { title, elements: prev?.elements || [] };
        });
      });

      socket.on(
        'set_sidebar_elements',
        ({ elements, key }: { elements: IMessageElement[]; key?: string }) => {
          if (!elements.length) {
            setSideView(undefined);
          } else {
            elements.forEach((element) => {
              if (!element.url && element.chainlitKey) {
                element.url = client.getElementUrl(
                  element.chainlitKey,
                  sessionId
                );
              }
            });
            setSideView((prev) => {
              if (prev?.key === key) return prev;
              return { title: prev?.title || '', elements: elements, key };
            });
          }
        }
      );

      socket.on('element', (element: IElement) => {
        if (!element.url && element.chainlitKey) {
          element.url = client.getElementUrl(element.chainlitKey, sessionId);
        }

        if (element.type === 'tasklist') {
          setTasklists((old) => {
            const index = old.findIndex((e) => e.id === element.id);
            if (index === -1) {
              return [...old, element];
            } else {
              return [...old.slice(0, index), element, ...old.slice(index + 1)];
            }
          });
        } else {
          setElements((old) => {
            const index = old.findIndex((e) => e.id === element.id);
            if (index === -1) {
              return [...old, element];
            } else {
              return [...old.slice(0, index), element, ...old.slice(index + 1)];
            }
          });
        }
      });

      socket.on('remove_element', (remove: { id: string }) => {
        setElements((old) => {
          return old.filter((e) => e.id !== remove.id);
        });
        setTasklists((old) => {
          return old.filter((e) => e.id !== remove.id);
        });
      });

      socket.on('action', (action: IAction) => {
        // Upsert by id: a re-emitted action (ask restored after reconnect)
        // must not duplicate a button that is still in the state.
        setActions((old) => {
          const index = old.findIndex((a) => a.id === action.id);
          if (index === -1) {
            return [...old, action];
          }
          return [...old.slice(0, index), action, ...old.slice(index + 1)];
        });
      });

      socket.on('remove_action', (action: IAction) => {
        setActions((old) => {
          const index = old.findIndex((a) => a.id === action.id);
          if (index === -1) return old;
          return [...old.slice(0, index), ...old.slice(index + 1)];
        });
      });

      socket.on('token_usage', (count: number) => {
        setTokenCount((old) => old + count);
      });

      socket.on('window_message', (data: any) => {
        if (window.parent) {
          window.parent.postMessage(data, '*');
        }
      });

      socket.on('toast', (data: { message: string; type: string }) => {
        if (!data.message) {
          console.warn('No message received for toast.');
          return;
        }

        switch (data.type) {
          case 'info':
            toast.info(data.message);
            break;
          case 'error':
            toast.error(data.message);
            break;
          case 'success':
            toast.success(data.message);
            break;
          case 'warning':
            toast.warning(data.message);
            break;
          default:
            toast(data.message);
            break;
        }
      });
    },
    // Stable length: React forbids a dep array that changes size between
    // renders, so the hot-swap case neutralizes the value instead of
    // dropping the entry.
    [
      setSession,
      sessionId,
      idToResume,
      hotSwap ? null : chatProfile,
      resetSessionId
    ]
  );

  const connect = useCallback(debounce(_connect, 200), [_connect]);

  const disconnect = useCallback(() => {
    if (session?.socket) {
      session.socket.removeAllListeners();
      session.socket.close();
    }
  }, [session]);

  // Ask the server to switch in place. The atom is NOT updated
  // optimistically: it follows chat_profile_changed only, so a refusal
  // leaves nothing to roll back and the client can never show a profile the
  // server is not running.
  const switchChatProfile = useCallback(
    (name: string) => {
      if (!session?.socket?.connected) return false;
      session.socket.emit('switch_chat_profile', { chatProfile: name });
      return true;
    },
    [session]
  );

  return {
    connect,
    disconnect,
    session,
    sessionId,
    chatProfile,
    idToResume,
    setChatProfile,
    switchChatProfile,
    hotSwapChatProfile: hotSwap
  };
};

export { useChatSession };
