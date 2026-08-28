import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRecoilState, useRecoilValue } from 'recoil';
import { toast } from 'sonner';

import {
  ErrorCode,
  protocolErrorState,
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
  const [protocolError, setProtocolError] = useRecoilState(protocolErrorState);
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

  // The wire has one error channel now; a resume failure is the
  // `thread_not_found` code on it.
  useEffect(() => {
    if (protocolError?.code !== ErrorCode.THREAD_NOT_FOUND) return;
    toast.error(
      protocolError.message
        ? "Couldn't resume chat: " + protocolError.message
        : "Couldn't resume chat"
    );
    navigate('/');
    setProtocolError(undefined);
  }, [protocolError]);

  return null;
}
