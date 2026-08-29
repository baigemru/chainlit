import { useCallback, useContext } from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';
import {
  actionState,
  askUserState,
  currentThreadIdState,
  elementState,
  firstUserInteraction,
  loadingState,
  messagesState,
  protocolErrorState,
  sessionDescriptorState,
  sessionIdState,
  sideViewState,
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

  const setFirstUserInteraction = useSetRecoilState(firstUserInteraction);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setElements = useSetRecoilState(elementState);
  const setTasklists = useSetRecoilState(tasklistState);
  const setActions = useSetRecoilState(actionState);
  const setIdToResume = useSetRecoilState(threadIdToResumeState);
  const setSideView = useSetRecoilState(sideViewState);
  const setCurrentThreadId = useSetRecoilState(currentThreadIdState);
  const setAskUser = useSetRecoilState(askUserState);
  const setProtocolError = useSetRecoilState(protocolErrorState);

  /**
   * Leave this session behind and start another one.
   *
   * The successor is minted in a single write, so the connect effect never
   * sees a half-built one: `next` names the parts the caller has an opinion
   * about — a hand-off's server-minted session id, the profile it switches
   * to, the thread the new session resumes — and the rest is fresh.
   */
  const clear = useCallback(
    (next: Partial<SessionDescriptor> = {}) => {
      transport.send({ t: 'session.clear' });
      // Relinquished right away rather than left for the attach that follows
      // to close: until the new descriptor is attached, frames arriving on
      // the old socket would land in a chat that has already been wiped.
      transport.detach();
      setDescriptor((old) => ({
        sessionId: next.sessionId ?? uuidv4(),
        chatProfile: next.chatProfile ?? old.chatProfile,
        threadId: next.threadId
      }));
      // The old session is gone; a lingering ask would hold a dead callback
      // (and possibly an awaitingReply lock) forever.
      setAskUser(undefined);
      setFirstUserInteraction(undefined);
      setProtocolError(undefined);
      setMessages([]);
      setElements([]);
      setTasklists([]);
      setActions([]);
      setSideView(undefined);
      setCurrentThreadId(undefined);
    },
    [
      transport,
      setDescriptor,
      setAskUser,
      setFirstUserInteraction,
      setProtocolError,
      setMessages,
      setElements,
      setTasklists,
      setActions,
      setSideView,
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

    transport.send({ t: 'stop' });
  }, [transport, setLoading, setMessages]);

  const uploadFile = useCallback(
    (file: File, onProgress: (progress: number) => void, parentId?: string) => {
      return client.uploadFile(file, onProgress, sessionId, parentId);
    },
    [sessionId]
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
