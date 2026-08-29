import { debounce } from 'lodash';
import { useCallback, useContext, useEffect, useRef } from 'react';
import {
  useRecoilCallback,
  useRecoilState,
  useRecoilValue,
  useResetRecoilState,
  useSetRecoilState
} from 'recoil';
import { toast } from 'sonner';
import {
  actionState,
  askUserState,
  chatProfileState,
  currentThreadIdState,
  elementState,
  firstUserInteraction,
  loadingState,
  messagesState,
  protocolErrorState,
  sessionIdState,
  sessionIdStorage,
  sessionState,
  sideViewState,
  tasklistState,
  threadIdToResumeState
} from 'src/state';
import {
  IAction,
  IAsk,
  IAskElementResponse,
  IFileRef,
  IMessageElement,
  IStep,
  ITasklistElement
} from 'src/types';
import { pruneAskActions } from 'src/utils/ask';
import {
  addMessage,
  deleteMessageById,
  stampChatProfile,
  updateMessageById,
  updateMessageContentById
} from 'src/utils/message';
import {
  toAction,
  toElement,
  toStep,
  toStepPatch,
  toThread,
  toWireStep
} from 'src/utils/wire';

import { ChainlitContext } from './context';
import type {
  AskReplyValue,
  Hello,
  ProtocolAskSpec,
  ServerMsgHandlers
} from './protocol';
import { CloseCode } from './protocol';
import { ChainlitSocket, websocketUrl } from './socket';

// True once any connection succeeded in this page's lifetime. Reported to
// the server in `hello` so it can distinguish a reconnect of a loaded page
// (UI state intact) from a fresh page load that needs a full restore of a
// pending ask's transcript/actions/element.
let pageHasEstablishedConnection = false;

// For embedders that unmount and remount the whole widget (copilot): the
// remounted UI starts empty, so the next connect must be treated as a
// fresh load again or the server would skip the full restore.
const resetPageConnectionFlag = () => {
  pageHasEstablishedConnection = false;
};
export { resetPageConnectionFlag };

/**
 * Turn a wire ask spec into the flat shape the ask components read.
 *
 * The two are the same object: the union's per-kind fields (`keys`,
 * `maxFiles`, `elementId`) are exactly the optional fields of `IAsk['spec']`,
 * and the discriminator is spelled `type` on both sides.
 */
const toAskSpec = (spec: ProtocolAskSpec): IAsk['spec'] =>
  ({ ...spec }) as IAsk['spec'];

const useChatSession = () => {
  const client = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const resetSessionId = useResetRecoilState(sessionIdState);
  // One-shot guard: a persisted session id the server refuses gets replaced
  // once; a second refusal in a row surfaces as an error instead of looping.
  const authFailureHandledRef = useRef(false);

  const [session, setSession] = useRecoilState(sessionState);
  // The transport being replaced, read at connect time. A ref rather than a
  // dependency of `_connect`: the session atom changes on every status
  // transition, and a connect that rebuilt itself on each would loop.
  const sessionRef = useRef(session);
  sessionRef.current = session;
  // The session id the live transport was built for. `connect` is called
  // from an effect whose inputs flicker (the config is dropped and refetched
  // on every profile change), and each call used to rebuild the socket --
  // a second connection on the same session while the first was still up.
  const openForRef = useRef<string>();
  const setFirstUserInteraction = useSetRecoilState(firstUserInteraction);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setAskUser = useSetRecoilState(askUserState);
  const setSideView = useSetRecoilState(sideViewState);
  const setElements = useSetRecoilState(elementState);
  const setTasklists = useSetRecoilState(tasklistState);
  const setActions = useSetRecoilState(actionState);
  const setProtocolError = useSetRecoilState(protocolErrorState);
  const [chatProfile, setChatProfile] = useRecoilState(chatProfileState);
  // The handlers below are registered once per connect and would otherwise
  // close over the profile that was active at that moment; the ref always
  // carries the current one, so every incoming message is stamped with the
  // profile it is actually generated under.
  const chatProfileRef = useRef(chatProfile);
  chatProfileRef.current = chatProfile;
  const idToResume = useRecoilValue(threadIdToResumeState);

  const [currentThreadId, setCurrentThreadId] =
    useRecoilState(currentThreadIdState);
  // The thread id travels in every `hello`, and it changes without the
  // socket being rebuilt — hence a ref rather than a dependency. This
  // replaces the mutation of socket.io's `auth` dict.
  const currentThreadIdRef = useRef(currentThreadId);
  currentThreadIdRef.current = currentThreadId;

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

  // Ending an ask is addressed now: `ask.end` names the step it ends, so a
  // clear that arrives after a successor ask took the slot is dropped here
  // instead of taking the live form down with it.
  const endAsk = useRecoilCallback(
    ({ set, snapshot }) =>
      (stepId: string) => {
        const current = snapshot.getLoadable(askUserState).valueMaybe();
        if (current && current.spec.stepId !== stepId) return;
        set(actionState, (old) => pruneAskActions(old, current));
        set(askUserState, undefined);
      },
    []
  );

  const _connect = useCallback(
    async ({ userEnv }: { userEnv: Record<string, string> }) => {
      if (
        openForRef.current === sessionId &&
        sessionRef.current?.socket.alive
      ) {
        // Already connected, or reconnecting on its own, for this very
        // session: nothing to rebuild.
        return;
      }
      openForRef.current = sessionId;
      try {
        await client.stickyCookie(sessionId);
      } catch (err) {
        console.error(`Failed to set sticky session cookie: ${err}`);
      }

      const buildHello = (): Hello => ({
        t: 'hello',
        sessionId,
        clientType: client.type,
        threadId: currentThreadIdRef.current || idToResume || undefined,
        chatProfile: chatProfileRef.current || undefined,
        userEnv,
        // True only on the very first connect after a full page load: the
        // server restores the old session then only to rescue a live
        // pending ask; otherwise a reload means a fresh chat. Flipped to
        // false after the first successful handshake so automatic transport
        // reconnects restore the session unconditionally.
        pageLoad: !pageHasEstablishedConnection
      });

      const socket = new ChainlitSocket({
        url: websocketUrl(client.httpEndpoint),
        hello: buildHello,
        onStatus: (status) => {
          // A new object on every transition so consumers of sessionState
          // re-render and re-read `socket.connected`, which is a getter on
          // a mutable transport rather than a value in the atom.
          setSession((old) =>
            old?.socket === socket
              ? { socket, error: status === 'ready' ? false : old.error }
              : old
          );
        },
        onClose: ({ code, opened, terminal }) => {
          if (
            terminal &&
            code === CloseCode.SESSION_FORBIDDEN &&
            !authFailureHandledRef.current
          ) {
            // The persisted session id belongs to a session this user may
            // not claim (e.g. someone else logged in within this tab). Mint
            // a fresh id instead of retrying against the same refusal
            // forever — the id change rebuilds the socket. Once only: a
            // refusal for another reason would repeat with the new id and
            // must surface as an error.
            authFailureHandledRef.current = true;
            resetSessionId();
            return;
          }
          // A connection that never opened is a failed attempt — an upgrade
          // refused before the server accepted it, where no close frame
          // exists to read, or an unreachable server. That and a close the
          // transport will not retry are the two errors the UI reacts to; a
          // drop it is about to retry is not one, and the retry that fails
          // will report itself.
          if (opened && !terminal) return;
          setSession((old) => (old ? { ...old, error: true } : old));
        }
      });

      const handlers: ServerMsgHandlers = {
        // ---- lifecycle -------------------------------------------------
        'session.ready': (msg) => {
          pageHasEstablishedConnection = true;
          authFailureHandledRef.current = false;
          if (msg.chatProfile) setChatProfile(msg.chatProfile);
        },

        error: (msg) => {
          // One channel for every refusal the server can name. Consumers
          // filter on `code`; nothing here decides on their behalf.
          setProtocolError(msg);
          console.warn(`Server error (${msg.code}): ${msg.message ?? ''}`);
        },

        hb: () => {
          // Answered by the transport itself, before the fan-out.
        },

        reload: () => {
          socket.send({ t: 'session.clear' });
          try {
            // The server asked for a clean restart (dev hot-reload): drop
            // the persisted id so the reloaded page cannot race the clear
            // and resurrect the session it was told to leave.
            sessionStorage.removeItem(sessionIdStorage.key);
          } catch (_error) {
            // Storage unavailable — the reload proceeds regardless.
          }
          window.location.reload();
        },

        // ---- steps -----------------------------------------------------
        'step.upsert': ({ step }) => {
          const message = toStep(step);
          setMessages((old) =>
            // An upsert states the whole step, so `wait` is stated too:
            // the explicit (possibly undefined) value overwrites any stored
            // one instead of surviving the merge.
            addMessage(old, {
              ...stampChatProfile(message, chatProfileRef.current),
              wait: message.wait
            })
          );
        },

        'step.update': ({ step }) => {
          const patch = toStepPatch(step);
          setMessages((old) =>
            // Only the fields the frame carries are written: an absent one
            // is "no opinion", an explicit null became an undefined that
            // clears the stored value.
            updateMessageById(old, step.id, patch)
          );
        },

        'step.delete': ({ stepId }) => {
          setMessages((old) => deleteMessageById(old, stepId));
        },

        'step.stream.start': ({ step }) => {
          const message = toStep(step);
          setMessages((old) =>
            // Same as an upsert: a stream start for an id that was in wait
            // mode must clear the stored `wait`, or the rotation text would
            // hide the streamed tokens.
            addMessage(old, {
              ...stampChatProfile(message, chatProfileRef.current),
              wait: message.wait
            })
          );
        },

        'step.stream.token': ({ id, token, isSequence, isInput }) => {
          setMessages((old) =>
            updateMessageContentById(old, id, token, !!isSequence, !!isInput)
          );
        },

        // ---- elements and actions --------------------------------------
        'element.upsert': (msg) => {
          const element = toElement(msg.element);
          if (!element.url && element.chainlitKey) {
            element.url = client.getElementUrl(element.chainlitKey, sessionId);
          }
          if (element.type === 'tasklist') {
            setTasklists((old) => upsertById(old, element as ITasklistElement));
          } else {
            setElements((old) => upsertById(old, element as IMessageElement));
          }
        },

        'element.remove': ({ id }) => {
          setElements((old) => old.filter((e) => e.id !== id));
          setTasklists((old) => old.filter((e) => e.id !== id));
        },

        'action.add': (msg) => {
          // Upsert by id: a re-emitted action (an ask restored after a
          // reconnect) must not duplicate a button already in the state.
          setActions((old) => upsertById(old, toAction(msg.action)));
        },

        'action.remove': ({ id }) => {
          setActions((old) => old.filter((a) => a.id !== id));
        },

        // ---- asks ------------------------------------------------------
        'ask.start': ({ spec, step }) => {
          const askSpec = toAskSpec(spec);
          const reply = (
            payload: IStep | IFileRef[] | IAction | IAskElementResponse
          ) => {
            // A plain message rather than a request/response ack: it is
            // buffered while the transport is down and redelivered after
            // the reconnect, so a click during a network blip is not lost.
            socket.send({
              t: 'ask.reply',
              stepId: askSpec.stepId,
              value: toAskReplyValue(askSpec.type, payload)
            });
            setAskUser((prev) =>
              prev && prev.spec.stepId === askSpec.stepId
                ? { ...prev, awaitingReply: true }
                : prev
            );
          };
          // A foreign ask replacing the previous one orphans that ask's
          // buttons — drop them. The step id guard inside makes the SAME
          // ask's re-emit (reconnect restore) a no-op here.
          pruneStaleAskActions(askSpec.stepId);
          const message = toStep(step);
          // A re-emitted ask (reconnect restore) simply rebinds the form to
          // the live socket; addMessage upserts the message by id.
          setAskUser({
            spec: askSpec,
            callback: reply,
            parentId: message.parentId
          });
          setMessages((old) =>
            addMessage(old, stampChatProfile(message, chatProfileRef.current))
          );
          setLoading(false);
        },

        'ask.end': ({ stepId, reason }) => {
          endAsk(stepId);
          if (reason === 'timeout') setLoading(false);
        },

        // ---- task indicator --------------------------------------------
        'task.indicator': ({ running }) => {
          setLoading(running);
        },

        // ---- threads ---------------------------------------------------
        'thread.resume': ({ thread }) => {
          const resumed = toThread(thread);
          const isReadOnlyView = Boolean(
            (resumed.metadata as Record<string, unknown> | undefined)
              ?.viewer_read_only
          );
          if (!isReadOnlyView && idToResume && resumed.id !== idToResume) {
            window.location.href = `/thread/${resumed.id}`;
          }
          if (!isReadOnlyView && idToResume) {
            setCurrentThreadId(resumed.id);
          }
          let messages: IStep[] = [];
          for (const step of resumed.steps) {
            messages = addMessage(messages, step);
          }
          if (resumed.metadata?.chat_profile) {
            setChatProfile(resumed.metadata.chat_profile);
          }
          setMessages(messages);
          const elements = resumed.elements || [];
          setTasklists(
            (elements as ITasklistElement[]).filter(
              (e) => e.type === 'tasklist'
            )
          );
          setElements(
            (elements as IMessageElement[]).filter(
              (e) => ['avatar', 'tasklist'].indexOf(e.type) === -1
            )
          );
        },

        'thread.first_interaction': ({ interaction, threadId }) => {
          setFirstUserInteraction(interaction);
          setCurrentThreadId(threadId);
        },

        'thread.parent': () => {
          // Router-dependent: ThreadReturnListener subscribes to this one.
        },

        'thread.open': () => {
          // Router-dependent: ThreadReturnListener subscribes to this one.
        },

        // ---- profile handoff and sidebar -------------------------------
        'session.handoff': () => {
          // Tears the session down and adopts a server-minted successor id.
          // Router-dependent: ChatProfileSwitchListener owns it.
        },

        'sidebar.set': (msg) => {
          setSideView((prev) => {
            // Absence and null are different instructions here: a field the
            // frame leaves out means "leave it alone", an explicit null on
            // the title or the key clears it.
            const hasTitle = 'title' in msg;
            const hasKey = 'key' in msg;
            const hasElements = 'elements' in msg;

            const incoming = hasElements
              ? (msg.elements ?? []).map((raw) => {
                  const element = toElement(raw) as IMessageElement;
                  if (!element.url && element.chainlitKey) {
                    element.url = client.getElementUrl(
                      element.chainlitKey,
                      sessionId
                    );
                  }
                  return element;
                })
              : undefined;

            // `elements` has no null form: an empty list closes the sidebar.
            if (incoming && !incoming.length) return undefined;

            const title = hasTitle ? (msg.title ?? '') : prev?.title || '';
            const key = hasKey ? (msg.key ?? undefined) : prev?.key;

            // A sidebar already open under this key keeps the elements it is
            // showing: `ElementSidebar.set_elements(key=...)` promises that,
            // and replacing the array would remount a custom element and
            // throw away whatever the user had typed into it.
            const keepElements =
              !!incoming && !!prev && key !== undefined && prev.key === key;
            const elements =
              incoming && !keepElements ? incoming : prev?.elements || [];

            if (
              prev &&
              prev.title === title &&
              prev.key === key &&
              prev.elements === elements
            ) {
              return prev;
            }
            return { title, elements, key };
          });
        },

        // ---- misc ------------------------------------------------------
        toast: ({ message, type }) => {
          if (!message) {
            console.warn('No message received for toast.');
            return;
          }
          switch (type) {
            case 'info':
              toast.info(message);
              break;
            case 'error':
              toast.error(message);
              break;
            case 'success':
              toast.success(message);
              break;
            case 'warning':
              toast.warning(message);
              break;
            default:
              toast(message);
              break;
          }
        }
      };

      socket.subscribe((message) => {
        // The table is a mapped type over every tag in the union, so a new
        // message on the wire is a compile error here rather than silence
        // at runtime.
        (handlers[message.t] as (m: typeof message) => void)(message);
      });

      if (
        typeof window !== 'undefined' &&
        (window as any).Cypress &&
        client.type !== 'copilot'
      ) {
        // Exposed for e2e tests to simulate transport drops, shaped like the
        // socket.io handle the specs were written against. Only under
        // Cypress, and never for the copilot widget: a handle on the user's
        // socket must not leak to page scripts in production.
        (window as any).__chainlitSocket = {
          get connected() {
            return socket.connected;
          },
          get sendBuffer() {
            return socket.sendBuffer;
          },
          connect: () => socket.connect(),
          close: () => socket.close(),
          io: {
            reconnection: (enabled?: boolean) =>
              socket.setReconnection(enabled),
            engine: { close: () => socket.drop() }
          }
        };
      }

      // Closed *before* the atom is written, and outside the updater. A
      // close fires the old socket's `onStatus`, which writes this same
      // atom; Recoil forbids an update from inside an updater and throws --
      // which used to abort the connect before `socket.connect()` ran, and
      // every socket rebuild (a profile change, a resume that changed the
      // profile) left the page spinning with no transport at all.
      sessionRef.current?.socket.close();
      // Written to the ref as well, not only the atom: a second connect
      // fired before the next render must close *this* socket, not the one
      // the ref still holds from the last render.
      sessionRef.current = { socket };
      setSession({ socket });

      socket.connect();
    },
    // Not the profile. It travels in `hello`, read through the ref at the
    // moment of the attempt; a deliberate switch goes through `clear()`,
    // whose new session id rebuilds the socket. The profile the *server*
    // announces (`session.ready`, `thread.resume`) must not: rebuilding the
    // transport to tell the server the profile it just told us opened a
    // second socket on the same session while the first was still up.
    [setSession, sessionId, idToResume]
  );

  const connect = useCallback(debounce(_connect, 200), [_connect]);
  // A trailing call on a superseded wrapper would open a socket for a
  // session that `clear()` has already left behind.
  useEffect(() => () => connect.cancel(), [connect]);

  const disconnect = useCallback(() => {
    session?.socket.close();
  }, [session]);

  return {
    connect,
    disconnect,
    session,
    sessionId,
    chatProfile,
    idToResume,
    setChatProfile
  };
};

/** Insert or replace by id, preserving position. */
const upsertById = <T extends { id: string }>(items: T[], item: T): T[] => {
  const index = items.findIndex((existing) => existing.id === item.id);
  if (index === -1) return [...items, item];
  return [...items.slice(0, index), item, ...items.slice(index + 1)];
};

/**
 * Stamp the reply's `kind`.
 *
 * The wire's reply value is a tagged union — a text answer, a file list, an
 * action and an element submission are all objects, so nothing but the tag
 * tells them apart. The ask's own spec already says which one is coming, so
 * that is what decides it, rather than sniffing the payload's shape.
 */
const toAskReplyValue = (
  type: IAsk['spec']['type'],
  payload: IStep | IFileRef[] | IAction | IAskElementResponse
): AskReplyValue => {
  switch (type) {
    case 'file':
      return { kind: 'file', files: payload as IFileRef[] };
    case 'action': {
      const action = payload as IAction;
      return {
        kind: 'action',
        action: {
          id: action.id,
          name: action.name,
          payload: action.payload,
          label: action.label,
          tooltip: action.tooltip,
          icon: action.icon,
          forId: action.forId
        }
      };
    }
    case 'element': {
      const response = payload as IAskElementResponse;
      return {
        kind: 'element',
        submitted: response.submitted,
        props: response.props ?? {}
      };
    }
    case 'text':
    default:
      return { kind: 'text', step: toWireStep(payload as IStep) };
  }
};

export { useChatSession };
