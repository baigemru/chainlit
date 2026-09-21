import { useCallback, useContext } from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';
import {
  acceptingState,
  actionState,
  askUserState,
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
  threadIdToResumeState
} from 'src/state';
import { IFileRef, IStep } from 'src/types';
import { addMessage } from 'src/utils/message';
import { toWireStep } from 'src/utils/wire';
import { v4 as uuidv4 } from 'uuid';

import { ChainlitContext, useChatTransport } from './context';
import type { SessionDescriptor } from './transport';

type PartialBy<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

const useChatInteract = () => {
  const client = useContext(ChainlitContext);
  const transport = useChatTransport();
  const askUser = useRecoilValue(askUserState);
  const sessionId = useRecoilValue(sessionIdState);

  const setDescriptor = useSetRecoilState(sessionDescriptorState);
  const setSessionId = useSetRecoilState(sessionIdState);

  const setFirstUserInteraction = useSetRecoilState(firstUserInteraction);
  const setLoading = useSetRecoilState(loadingState);
  const setAccepting = useSetRecoilState(acceptingState);
  const setMessages = useSetRecoilState(messagesState);
  const setElements = useSetRecoilState(elementState);
  const setTasklists = useSetRecoilState(tasklistState);
  const setActions = useSetRecoilState(actionState);
  const setIdToResume = useSetRecoilState(threadIdToResumeState);
  const setElementSidebar = useSetRecoilState(elementSidebarState);
  const setCurrentThreadId = useSetRecoilState(currentThreadIdState);
  const setAskUser = useSetRecoilState(askUserState);
  const setProtocolError = useSetRecoilState(protocolErrorState);

  /**
   * Leave this session behind and start another one.
   *
   * The successor is minted in a single write, so the connect effect never
   * sees a half-built one: `next` names the parts the caller has an opinion
   * about — the thread the new session resumes, the profile it switches to —
   * and the rest is fresh.
   */
  const clear = useCallback(
    (next: Partial<SessionDescriptor> = {}) => {
      // The server's cue to tear the session down and give the thread up, not
      // just to cancel its work: a session left alive on that thread would be
      // handed straight back the next time the thread is opened, as an empty
      // screen instead of the resume from the database.
      transport.send({ t: 'session.clear' });
      // Relinquished right away rather than left for the attach that follows
      // to close: until the new descriptor is attached, frames arriving on
      // the old socket would land in a chat that has already been wiped.
      // `detach` also forgets the descriptor, which is what makes two clears
      // in a row two connections even when both name the same (or no) thread.
      transport.detach();
      setDescriptor((old) => ({
        chatProfile: next.chatProfile ?? old.chatProfile,
        threadId: next.threadId
      }));
      // The handle dies with the session it addressed. Left behind, a click
      // made before the successor's `session.ready` would call an action on
      // the session this one is abandoning.
      setSessionId(undefined);
      // The old session is gone; a lingering ask would hold a dead callback
      // (and possibly an awaitingReply lock) forever.
      setAskUser(undefined);
      setFirstUserInteraction(undefined);
      setProtocolError(undefined);
      setMessages([]);
      setElements([]);
      setTasklists([]);
      setActions([]);
      // Back to the default, not to `undefined`: the panel is a state,
      // and every reader below it is reading one.
      setElementSidebar({ slots: [], active: null, visible: false, rev: 0 });
      setCurrentThreadId(undefined);
    },
    [
      transport,
      setDescriptor,
      setSessionId,
      setAskUser,
      setFirstUserInteraction,
      setProtocolError,
      setMessages,
      setElements,
      setTasklists,
      setActions,
      setElementSidebar,
      setCurrentThreadId
    ]
  );

  const sendMessage = useCallback(
    (
      message: PartialBy<IStep, 'createdAt' | 'id'>,
      fileReferences: IFileRef[] = []
    ) => {
      if (!message.id) {
        message.id = uuidv4();
      }
      if (!message.createdAt) {
        message.createdAt = new Date().toISOString();
      }
      setMessages((oldMessages) => addMessage(oldMessages, message as IStep));

      transport.send({
        t: 'message.send',
        message: toWireStep(message as IStep),
        fileReferences
      });
    },
    [transport, setMessages]
  );

  const replyMessage = useCallback(
    (message: IStep) => {
      if (askUser) {
        // A reply is already in flight for this ask; a re-emitted ask
        // (reconnect) resets the flag and re-enables the composer.
        if (askUser.awaitingReply) return;
        if (askUser.parentId) message.parentId = askUser.parentId;
        setMessages((oldMessages) => addMessage(oldMessages, message));
        askUser.callback(message);
      }
    },
    [askUser]
  );

  const stopTask = useCallback(() => {
    setMessages((oldMessages) =>
      oldMessages.map((m) => {
        m.streaming = false;
        return m;
      })
    );

    setLoading(false);
    // Stop cancels background work too, so the composer is open either way:
    // set it here rather than wait for the server's own resync, for the same
    // optimism the line above is written in.
    setAccepting(true);

    transport.send({ t: 'stop' });
  }, [transport, setLoading, setAccepting, setMessages]);

  const uploadFile = useCallback(
    (file: File, onProgress: (progress: number) => void, parentId?: string) => {
      if (!sessionId) {
        // No session to upload into: a drop onto a chat that is being
        // replaced, before its `session.ready` names the handle. Reported as
        // a failed upload rather than thrown, because both callers build
        // their attachment list inside a `map` and already `.catch` this
        // promise -- a throw would come out of a click handler instead, half
        // a list built.
        return {
          xhr: new XMLHttpRequest(),
          promise: Promise.reject(new Error('No live session to upload to'))
        };
      }
      return client.uploadFile(file, onProgress, sessionId, parentId);
    },
    [client, sessionId]
  );

  return {
    uploadFile,
    clear,
    replyMessage,
    sendMessage,
    stopTask,
    setIdToResume
  };
};

export { useChatInteract };
