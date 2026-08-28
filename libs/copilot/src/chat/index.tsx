import { useEffect, useRef } from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';

import {
  sessionIdState,
  threadIdToResumeState,
  useChatInteract,
  useChatSession
} from '@chainlit/react-client';

import { copilotThreadIdState } from '../state';
import ChatBody from './body';

export default function ChatWrapper() {
  const { connect, session, idToResume } = useChatSession();
  const { sendMessage } = useChatInteract();
  const copilotThreadId = useRecoilValue(copilotThreadIdState);
  const sessionId = useRecoilValue(sessionIdState);
  const setThreadIdToResume = useSetRecoilState(threadIdToResumeState);
  const hasConnected = useRef<boolean>(false);
  const lastConnectedThreadId = useRef<string | null>(null);
  const lastSessionId = useRef<string | null>(null);

  // A replaced session id (e.g. the server refused the persisted one and a
  // fresh id was minted) needs a new connection — re-arm the connect guard.
  useEffect(() => {
    if (
      hasConnected.current &&
      lastSessionId.current &&
      sessionId !== lastSessionId.current
    ) {
      hasConnected.current = false;
    }
    lastSessionId.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    if (!copilotThreadId) {
      return;
    }

    setThreadIdToResume(copilotThreadId);
  }, [copilotThreadId, setThreadIdToResume]);

  useEffect(() => {
    if (
      copilotThreadId &&
      lastConnectedThreadId.current &&
      copilotThreadId !== lastConnectedThreadId.current &&
      hasConnected.current
    ) {
      if (session?.socket?.connected) {
        session.socket.disconnect();
      }
      hasConnected.current = false;
      lastConnectedThreadId.current = null;
    }
  }, [copilotThreadId]);

  useEffect(() => {
    if (!copilotThreadId || !idToResume || copilotThreadId !== idToResume) {
      return;
    }

    if (hasConnected.current) {
      return;
    }

    hasConnected.current = true;
    lastConnectedThreadId.current = copilotThreadId;
    connect({ userEnv: {} });
  }, [copilotThreadId, idToResume, connect, sessionId]);

  useEffect(() => {
    // @ts-expect-error is not a valid prop
    window.sendChainlitMessage = sendMessage;
  }, [sendMessage]);

  return <ChatBody />;
}
