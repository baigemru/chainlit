import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRecoilState, useRecoilValue } from 'recoil';
import { toast } from 'sonner';

import {
  ErrorCode,
  protocolErrorState,
  useChatData,
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
  const { clear } = useChatInteract();
  const { idToResume } = useChatSession();
  const { error } = useChatData();
  const [protocolError, setProtocolError] = useRecoilState(protocolErrorState);
  const resetKeptTranscript = useResetKeptTranscript();

  // Read through a ref: the transition must not re-trigger the resume
  // effect, it only tells apart a return (which keeps the transcript) from
  // a plain open from the history (which starts with a blank screen).
  const transition = useRecoilValue(openThreadTransitionState);
  const transitionRef = useRef(transition);
  transitionRef.current = transition;

  // The descriptor is the guard. The config is dropped and refetched
  // whenever the profile changes -- which a resume does, the moment the
  // thread's profile comes back -- and this effect re-runs; a session
  // already opened to resume this thread is the answer, so it does nothing.
  useEffect(() => {
    if (!config?.threadResumable) return;
    if (idToResume === id) return;
    const isReturn =
      transitionRef.current?.threadId === id &&
      transitionRef.current.keepTranscript;
    if (!isReturn) resetKeptTranscript();
    // One write: the successor session and the thread it resumes are the
    // same decision.
    clear({ threadId: id });
    if (!config?.dataPersistence) {
      navigate('/');
    }
  }, [config?.threadResumable, id, idToResume]);

  useEffect(() => {
    if (id !== idToResume) {
      return;
    }
    // `error` no longer covers close 4409: a session taken over by another
    // window is not a resume that failed, and sending the user back to the
    // home screen for it would throw away the thread they asked for.
    if (error) {
      toast.error("Couldn't resume chat");
      // The descriptor is the guard above, so a resume that failed has to
      // let go of the thread it was for -- otherwise picking the same
      // thread out of the history again would find the guard already
      // satisfied and do nothing at all.
      clear();
      navigate('/');
    }
  }, [error, idToResume, id]);

  // The wire has one error channel now; a resume failure is the
  // `thread_not_found` code on it.
  useEffect(() => {
    // Only once this thread is the one the session was opened for. On the
    // commit that mounts this component the resume above has been issued
    // but not yet rendered, and an error left over from the previous
    // session -- which `clear()` is about to drop -- would be taken for
    // this resume's answer and bounce the user straight back home.
    if (id !== idToResume) return;
    if (protocolError?.code !== ErrorCode.THREAD_NOT_FOUND) return;
    toast.error(
      protocolError.message
        ? "Couldn't resume chat: " + protocolError.message
        : "Couldn't resume chat"
    );
    // Same release as above: the thread this session was opened for turned
    // out not to exist, and the next attempt at it must be a fresh one.
    clear();
    navigate('/');
    setProtocolError(undefined);
  }, [protocolError, idToResume, id]);

  return null;
}
