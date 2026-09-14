import { useEffect, useRef } from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';

import {
  threadIdToResumeState,
  useChatInteract,
  useChatSession,
  useChatTransport
} from '@chainlit/react-client';

import { copilotThreadIdState } from '../state';
import ChatBody from './body';

export default function ChatWrapper() {
  const { attach, descriptor } = useChatSession();
  const { sendMessage } = useChatInteract();
  const transport = useChatTransport();
  const copilotThreadId = useRecoilValue(copilotThreadIdState);
  const setCopilotThreadId = useSetRecoilState(copilotThreadIdState);
  const setThreadIdToResume = useSetRecoilState(threadIdToResumeState);

  // The widget has no address bar, so localStorage is where its request
  // lives — and it has to be corrected the same way the app's URL is. The
  // server may refuse the thread the widget offered (deleted, or another
  // user's) and continue in one of its own; left unwritten, every reload
  // would re-offer the refused id forever and the conversation would
  // restart each time.
  const copilotThreadIdRef = useRef(copilotThreadId);
  copilotThreadIdRef.current = copilotThreadId;
  useEffect(() => {
    return transport.onMessage((message) => {
      if (message.t !== 'session.ready' || !message.threadId) return;
      if (message.threadId === copilotThreadIdRef.current) return;
      // One reconnect follows: the new id reaches the descriptor through
      // the effect below, and a new thread is a new descriptor identity. It
      // converges — the next `session.ready` names the same thread and this
      // is a no-op.
      setCopilotThreadId(message.threadId);
    });
  }, [transport, setCopilotThreadId]);

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
