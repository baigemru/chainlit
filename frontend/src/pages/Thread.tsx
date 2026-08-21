import { useEffect } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { useRecoilValue, useSetRecoilState } from 'recoil';

import Page from 'pages/Page';

import {
  threadHistoryState,
  useChatMessages,
  useConfig
} from '@chainlit/react-client';

import AutoResumeThread from '@/components/AutoResumeThread';
import { Loader } from '@/components/Loader';
import { ReadOnlyThread } from '@/components/ReadOnlyThread';
import Chat from '@/components/chat';

import { openThreadTransitionState } from '@/state/chat';

export default function ThreadPage() {
  const { id } = useParams();
  const location = useLocation();
  const { config } = useConfig();

  const setThreadHistory = useSetRecoilState(threadHistoryState);

  const { threadId } = useChatMessages();
  const transition = useRecoilValue(openThreadTransitionState);

  const isCurrentThread = threadId === id;
  // A return to a parent thread keeps the transcript on screen while the
  // thread resumes; swapping the chat for a loader would blank it mid-way.
  const keepChatMounted =
    isCurrentThread ||
    (!!transition?.keepTranscript && transition.threadId === id);

  useEffect(() => {
    setThreadHistory((prev) => {
      if (prev?.currentThreadId === id) return prev;
      return { ...prev, currentThreadId: id };
    });
  }, [id]);

  const isSharedRoute = location.pathname.startsWith('/share/');

  return (
    <Page>
      <>
        {isSharedRoute ? <ReadOnlyThread id={id!} /> : null}
        {config?.threadResumable && !isCurrentThread && !isSharedRoute ? (
          <AutoResumeThread id={id!} />
        ) : null}
        {config?.threadResumable && !isSharedRoute ? (
          keepChatMounted ? (
            <Chat />
          ) : (
            <div className="flex flex-grow items-center justify-center">
              <Loader className="!size-6" />
            </div>
          )
        ) : null}
        {config && !config.threadResumable && !isSharedRoute ? (
          isCurrentThread ? (
            <Chat />
          ) : (
            <ReadOnlyThread id={id!} />
          )
        ) : null}
      </>
    </Page>
  );
}
