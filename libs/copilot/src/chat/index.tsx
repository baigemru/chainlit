import { useEffect } from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';

import {
  threadIdToResumeState,
  useChatInteract,
  useChatSession
} from '@chainlit/react-client';

import { copilotThreadIdState } from '../state';
import ChatBody from './body';

export default function ChatWrapper() {
  const { attach, descriptor } = useChatSession();
  const { sendMessage } = useChatInteract();
  const copilotThreadId = useRecoilValue(copilotThreadIdState);
  const setThreadIdToResume = useSetRecoilState(threadIdToResumeState);

  // The widget always resumes a thread of its own, so the thread the host
  // page names is part of the descriptor rather than something bolted on
  // after the connection is up.
  useEffect(() => {
    if (!copilotThreadId) return;
    setThreadIdToResume(copilotThreadId);
  }, [copilotThreadId, setThreadIdToResume]);

  // Attaching is idempotent by descriptor, so the three refs that used to
  // stand in for "have we connected, and what for" are gone: the only thing
  // to check is that the descriptor has caught up with the host page. A new
  // thread id is a new descriptor, which closes the old connection and opens
  // one for the new thread by itself.
  useEffect(() => {
    if (!copilotThreadId || descriptor.threadId !== copilotThreadId) return;
    attach(descriptor, { userEnv: {} });
  }, [attach, descriptor, copilotThreadId]);

  useEffect(() => {
    // @ts-expect-error is not a valid prop
    window.sendChainlitMessage = sendMessage;
  }, [sendMessage]);

  return <ChatBody />;
}
