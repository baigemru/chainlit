import { useCallback, useContext } from 'react';
import { useRecoilValue, useResetRecoilState, useSetRecoilState } from 'recoil';
import {
  actionState,
  askUserState,
  currentThreadIdState,
  elementState,
  firstUserInteraction,
  loadingState,
  messagesState,
  protocolErrorState,
  sessionIdState,
  sessionState,
  sideViewState,
  tasklistState,
  threadIdToResumeState
} from 'src/state';
import { IFileRef, IStep } from 'src/types';
import { addMessage } from 'src/utils/message';
import { toWireStep } from 'src/utils/wire';
import { v4 as uuidv4 } from 'uuid';

import { ChainlitContext } from './context';

type PartialBy<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

const useChatInteract = () => {
  const client = useContext(ChainlitContext);
  const session = useRecoilValue(sessionState);
  const askUser = useRecoilValue(askUserState);
  const sessionId = useRecoilValue(sessionIdState);

  const resetSessionId = useResetRecoilState(sessionIdState);

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

  const clear = useCallback(() => {
    session?.socket.send({ t: 'session.clear' });
    session?.socket.close();
    setIdToResume(undefined);
    resetSessionId();
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
  }, [session]);

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

      session?.socket.send({
        t: 'message.send',
        message: toWireStep(message as IStep),
        fileReferences
      });
    },
    [session?.socket]
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

    session?.socket.send({ t: 'stop' });
  }, [session?.socket]);

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
