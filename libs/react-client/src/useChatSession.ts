import { useCallback, useContext, useMemo, useRef } from 'react';
import { useRecoilCallback, useRecoilValue, useSetRecoilState } from 'recoil';
import { toast } from 'sonner';
import {
  actionState,
  askUserState,
  chatProfileState,
  currentThreadIdState,
  elementSidebarState,
  elementState,
  firstUserInteraction,
  loadingState,
  messagesState,
  protocolErrorState,
  sessionDescriptorState,
  sessionIdState,
  tasklistState,
  userState
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

import { isMobileViewport } from './breakpoint';
import { ChainlitContext, useChatTransport } from './context';
import { CloseCode } from './protocol';
import type {
  AskReplyValue,
  ProtocolAskSpec,
  ServerMsg,
  ServerMsgHandlers
} from './protocol';
import type { HelloPayload, SessionDescriptor, SessionSink } from './transport';

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
  const { chatProfile, threadId: idToResume } = descriptor;
  const sessionId = useRecoilValue(sessionIdState);
  const setSessionId = useSetRecoilState(sessionIdState);
  const setChatProfile = useSetRecoilState(chatProfileState);

  // The handle, captured in the frame flow rather than read off a render.
  // `element.upsert` mints its url from the session id and can follow
  // `session.ready` in the same tick; a handler closure rebuilt on the next
  // render would still hold the previous session's id -- or, on the first
  // connection, none at all -- and every element url would come out
  // `?session_id=undefined`. Written only by the `session.ready` handler,
  // never mirrored from a render: `session.ready` is the first frame of
  // every socket, so by the time anything reads this it is this
  // connection's id and nothing has to clear it.
  const sessionIdRef = useRef<string | undefined>(undefined);

  const setFirstUserInteraction = useSetRecoilState(firstUserInteraction);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setAskUser = useSetRecoilState(askUserState);
  const setElements = useSetRecoilState(elementState);
  const setTasklists = useSetRecoilState(tasklistState);
  const setActions = useSetRecoilState(actionState);
  const setProtocolError = useSetRecoilState(protocolErrorState);
  // The setter alone, not `useAuthState()`: ten components call this hook, and
  // that hook subscribes its caller to `userState` and `authState` for a value
  // nothing here reads. Same setter, no re-render.
  const setUser = useSetRecoilState(userState);

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

  // True from a `session.ready` until the first `sidebar.state` that follows
  // it: the restore's panel frame, and the only one the mobile rule below
  // may act on. Reset per connection rather than per page, because that is
  // the question being asked — "is this the panel a reload just got back?".
  const restoringSidebar = useRef(false);

  const applySidebarFrame = useRecoilCallback(
    ({ set, snapshot }) =>
      (msg: Extract<ServerMsg, { t: 'sidebar.state' }>) => {
        const known = new Map(
          (snapshot.getLoadable(elementState).valueMaybe() ?? []).map(
            (element) => [element.id, element]
          )
        );
        const slots = (msg.slots ?? []).map((slot) => ({
          id: slot.id,
          title: slot.title ?? '',
          closable: slot.closable ?? true,
          canvas: slot.canvas ?? false,
          elements: (slot.elementIds ?? [])
            .map((id) => {
              const element = known.get(id);
              if (!element) {
                console.warn(
                  `sidebar.state names element ${id}, which never arrived`
                );
              }
              return element;
            })
            .filter((element): element is IMessageElement => !!element)
        }));

        const wasRestore = restoringSidebar.current;
        restoringSidebar.current = false;
        // The one place this client branches on the screen. The panel is a
        // 95%-wide sheet on a phone, so a reload that put it straight back
        // would cover the question the user reloaded to answer. The
        // *viewport*, through the shared breakpoint -- the same question
        // `useIsMobile` answers when it decides to draw that sheet, and not
        // the `device` label the hello carries, which a `?device=pc` pin can
        // set against the layout. Hidden locally *and* on the server,
        // because the server must never decide anything by device: the same
        // conversation opened on a laptop has to behave the same way.
        const hideOnMobile =
          wasRestore &&
          msg.visible === true &&
          transport.opening.pageLoad &&
          isMobileViewport();

        const rev = msg.rev ?? 0;
        set(elementSidebarState, {
          slots,
          active: msg.active ?? null,
          visible: hideOnMobile ? false : (msg.visible ?? false),
          rev
        });
        if (hideOnMobile) {
          transport.send({ t: 'sidebar.user', op: 'hide', rev });
        }
      },
    [transport]
  );

  const handlers: ServerMsgHandlers = useMemo(
    () => ({
      // ---- lifecycle -------------------------------------------------
      'session.ready': (msg) => {
        // Before anything else: the frames that follow address the session
        // over HTTP with this.
        sessionIdRef.current = msg.sessionId;
        setSessionId(msg.sessionId);
        if (msg.chatProfile) setChatProfile(msg.chatProfile);
        // The server names the thread on every branch of the handshake --
        // kept, replaced, created, resumed -- and that answer outranks
        // whatever this client asked for. A resume the server refused comes
        // back as a *different* thread here rather than an error, and the
        // address bar follows it (ThreadAddressSync); without this write the
        // tab would sit on /thread/<refused> with the session in another
        // thread, and the same component would clear it on sight.
        setCurrentThreadId(msg.threadId ?? undefined);
        // The next `sidebar.state` is this connection's restore frame.
        restoringSidebar.current = true;
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
        // The server asked for a clean restart (dev hot-reload). `session.clear`
        // is the server's cue to tear the session down and forget the thread,
        // so the page that comes back cannot be handed the session it was
        // just told to leave.
        transport.send({ t: 'session.clear' });
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
          element.url = client.getElementUrl(
            element.chainlitKey,
            sessionIdRef.current!
          );
        }
        element.url = client.resolveElementUrl(element.url);
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
        // No redirect for a thread other than the one asked for. The server
        // answers a resume it will not grant by moving the session to a
        // thread of its own and naming it, and the address bar follows
        // (`ThreadAddressSync`); a full page load here would throw the live
        // session away to arrive at the address that listener was about to
        // write anyway.
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
        // A resumed thread's elements come straight from the database, so
        // their urls are the app-relative form — including the tasklists,
        // whose JSON is fetched by url exactly like an image's bytes.
        const elements = (resumed.elements || []).map((element) => ({
          ...element,
          url: client.resolveElementUrl(element.url)
        }));
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

      'sidebar.state': (msg) => {
        // Written whole, with the slots' elements looked up in the atom the
        // `element.upsert` handler above fills. The server sends those
        // upserts ahead of this frame on one FIFO queue, so an id it names
        // is an id we have — an id we do not is a server bug, and drawing a
        // hole for it would hide that.
        applySidebarFrame(msg);
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
      applySidebarFrame,
      chatProfile,
      client,
      endAsk,
      idToResume,
      pruneStaleAskActions,
      setActions,
      setAskUser,
      setChatProfile,
      setCurrentThreadId,
      setElements,
      setFirstUserInteraction,
      setLoading,
      setMessages,
      setProtocolError,
      setSessionId,
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
      onClose: (info) => {
        // Close 4401 is the socket learning what an HTTP 401 teaches, and it
        // gets the same answer `useApi` gives that status: forget the user.
        // Without this the tab sat on "reconnecting" forever -- the cookie
        // expired under an open page, the transport stopped (4401 is
        // terminal), and nothing told the app, so `isAuthenticated` stayed
        // true and the next run of the attach effect would re-attach the
        // closed transport and earn another 4401. Clearing the user flips
        // that gate shut and sends the page to /login, which is the only
        // thing that can actually fix it.
        if (info.code === CloseCode.UNAUTHENTICATED) setUser(null);
      }
    }),
    [handlers, setUser]
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
      {
        userEnv,
        device
      }: {
        userEnv?: Record<string, string>;
        device?: HelloPayload['device'];
      } = {}
    ) => {
      transport.setSink(sink);
      transport.attach(target, {
        // A session that has moved on offers the thread it is in, not the
        // one it was opened to resume.
        threadId: currentThreadId || target.threadId,
        chatProfile: target.chatProfile,
        userEnv,
        // Whoever renders knows the screen; this package does not. Passed
        // through untouched, and only for the funnel.
        device
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
