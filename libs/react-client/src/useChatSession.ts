import { useCallback, useContext, useMemo, useRef } from 'react';
import {
  useRecoilCallback,
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
  sessionDescriptorState,
  sessionIdState,
  sessionIdStorage,
  sideViewState,
  tasklistState
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

import { ChainlitContext, useChatTransport } from './context';
import type {
  AskReplyValue,
  ProtocolAskSpec,
  ServerMsg,
  ServerMsgHandlers
} from './protocol';
import { CloseCode } from './protocol';
import type { SessionDescriptor, SessionSink } from './transport';

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
  const transport = useChatTransport();
  const descriptor = useRecoilValue(sessionDescriptorState);
  const { sessionId, chatProfile, threadId: idToResume } = descriptor;
  const resetSessionId = useResetRecoilState(sessionIdState);
  const setChatProfile = useSetRecoilState(chatProfileState);
  // One-shot guard: a persisted session id the server refuses gets replaced
  // once; a second refusal in a row surfaces as an error instead of looping.
  const authFailureHandledRef = useRef(false);

  const setFirstUserInteraction = useSetRecoilState(firstUserInteraction);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setAskUser = useSetRecoilState(askUserState);
  const setSideView = useSetRecoilState(sideViewState);
  const setElements = useSetRecoilState(elementState);
  const setTasklists = useSetRecoilState(tasklistState);
  const setActions = useSetRecoilState(actionState);
  const setProtocolError = useSetRecoilState(protocolErrorState);

  // The thread the session is actually in, as opposed to the one it was
  // opened to resume. It changes without the connection being rebuilt, so it
  // travels as hello payload on the next attach rather than as part of the
  // descriptor.
  const currentThreadId = useRecoilValue(currentThreadIdState);
  const setCurrentThreadId = useSetRecoilState(currentThreadIdState);

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

  const handlers: ServerMsgHandlers = useMemo(
    () => ({
      // ---- lifecycle -------------------------------------------------
      'session.ready': (msg) => {
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
        transport.send({ t: 'session.clear' });
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
            ...stampChatProfile(message, chatProfile),
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
            ...stampChatProfile(message, chatProfile),
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
          transport.send({
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
          addMessage(old, stampChatProfile(message, chatProfile))
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
          (elements as ITasklistElement[]).filter((e) => e.type === 'tasklist')
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
    }),
    [
      chatProfile,
      client,
      endAsk,
      idToResume,
      pruneStaleAskActions,
      sessionId,
      setActions,
      setAskUser,
      setChatProfile,
      setCurrentThreadId,
      setElements,
      setFirstUserInteraction,
      setLoading,
      setMessages,
      setProtocolError,
      setSideView,
      setTasklists,
      transport
    ]
  );

  // One slot on the transport, not a subscription: `step.stream.token`
  // appends, so a table registered twice would double every streamed token —
  // and every component in the tree calls this hook. Only the one that
  // attaches installs it, and installing is a write, so doing it twice is
  // still once.
  const sink: SessionSink = useMemo(
    () => ({
      onFrame: (message: ServerMsg) => {
        // The table is a mapped type over every tag in the union, so a new
        // message on the wire is a compile error here rather than silence
        // at runtime.
        (handlers[message.t] as (m: typeof message) => void)(message);
      },
      onClose: ({ code, terminal }) => {
        if (
          terminal &&
          code === CloseCode.SESSION_FORBIDDEN &&
          !authFailureHandledRef.current
        ) {
          // The persisted session id belongs to a session this user may not
          // claim (e.g. someone else logged in within this tab). Mint a
          // fresh id instead of retrying against the same refusal forever —
          // the id change is a new descriptor, which reconnects. Once only:
          // a refusal for another reason would repeat with the new id and
          // must surface as an error.
          authFailureHandledRef.current = true;
          resetSessionId();
        }
      }
    }),
    [handlers, resetSessionId]
  );

  /**
   * Speak for this descriptor.
   *
   * Idempotent, so the caller is an effect that simply states the intent
   * whenever its inputs settle — there is nothing left for it to guard
   * against, and nothing left to debounce.
   */
  const attach = useCallback(
    (
      target: SessionDescriptor,
      { userEnv }: { userEnv?: Record<string, string> } = {}
    ) => {
      transport.setSink(sink);
      transport.attach(target, {
        // A session that has moved on offers the thread it is in, not the
        // one it was opened to resume.
        threadId: currentThreadId || target.threadId,
        chatProfile: target.chatProfile,
        userEnv
      });
    },
    [transport, sink, currentThreadId]
  );

  return {
    attach,
    detach: transport.detach,
    descriptor,
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
