import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRecoilState, useRecoilValue } from 'recoil';
import { toast } from 'sonner';

import {
  resumeThreadErrorState,
  useChatInteract,
  useChatSession,
  useConfig
} from '@chainlit/react-client';

import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { openThreadTransitionState } from '@/state/chat';

interface Props {
  id: string;
}

export default function AutoResumeThread({ id }: Props) {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { clear, setIdToResume } = useChatInteract();
  const { session, idToResume } = useChatSession();
  const [resumeThreadError, setResumeThreadError] = useRecoilState(
    resumeThreadErrorState
  );
  const resetKeptTranscript = useResetKeptTranscript();

  // Read through a ref: the transition must not re-trigger the resume
  // effect, it only tells apart a return (which keeps the transcript) from
  // a plain open from the history (which starts with a blank screen).
  const transition = useRecoilValue(openThreadTransitionState);
  const transitionRef = useRef(transition);
  transitionRef.current = transition;

  useEffect(() => {
    if (!config?.threadResumable) return;
    const isReturn =
      transitionRef.current?.threadId === id &&
      transitionRef.current.keepTranscript;
    if (!isReturn) resetKeptTranscript();
    clear();
    setIdToResume(id);
    if (!config?.dataPersistence) {
      navigate('/');
    }
  }, [config?.threadResumable, id]);

  useEffect(() => {
    if (id !== idToResume) {
      return;
    }
    if (session?.error) {
      toast.error("Couldn't resume chat");
      navigate('/');
    }
  }, [session, idToResume, id]);

  useEffect(() => {
    if (resumeThreadError) {
      toast.error("Couldn't resume chat: " + resumeThreadError);
      navigate('/');
      setResumeThreadError(undefined);
    }
  }, [resumeThreadError]);

  return null;
}
