import { isThreadAvailable } from '@/lib/openThread';
import { useCallback, useContext } from 'react';
import { flushSync } from 'react-dom';
// Deliberately the raw hook: the local Translator wrapper returns '...' for a
// missing key before t() runs, which would defeat the defaultValue below.
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useRecoilValue, useSetRecoilState } from 'recoil';
import { toast } from 'sonner';
import { v4 as uuidv4 } from 'uuid';

import {
  ChainlitContext,
  IStep,
  askUserState,
  callFnState,
  currentThreadIdState,
  loadingState,
  messagesState,
  useConfig
} from '@chainlit/react-client';

import { freezeStreaming } from '@/components/chat/MessagesContainer/transcript';

import {
  IAttachment,
  IChatBoundary,
  attachmentsState,
  chatBoundariesState,
  collapsedExcursionsState,
  keptExcursionsState,
  openThreadTransitionState,
  persistentCommandState
} from '@/state/chat';

// Guards the async window between the availability check and the state
// handover: module-level because the socket listener and the composer button
// call openThread from different components. Once the transition state is
// set it takes over; ThreadReturnListener clears it when the thread becomes
// current or the resume fails.
let pendingOpen = false;

/**
 * Opens an existing thread of the current user the same way a click in the
 * history list does — navigation to /thread/:id, teardown, resume session,
 * profile restore — optionally keeping the messages currently on screen
 * above a return divider (the same divider mechanism `set_chat_profile`
 * uses). Serves both the `open_thread` socket event and the composer's
 * return-to-parent button. Router-dependent: only mount it under the router
 * (the copilot widget routes the button through openThreadRequestState
 * instead).
 */
export const useOpenThread = () => {
  const navigate = useNavigate();
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const { t } = useTranslation();

  const currentThreadId = useRecoilValue(currentThreadIdState);
  const transition = useRecoilValue(openThreadTransitionState);

  const setAskUser = useSetRecoilState(askUserState);
  const setCallFn = useSetRecoilState(callFnState);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const setPersistentCommand = useSetRecoilState(persistentCommandState);
  const setBoundaries = useSetRecoilState(chatBoundariesState);
  const setKeptExcursions = useSetRecoilState(keptExcursionsState);
  const setCollapsedExcursions = useSetRecoilState(collapsedExcursionsState);
  const setTransition = useSetRecoilState(openThreadTransitionState);

  const threadResumable = config?.threadResumable;

  return useCallback(
    async (threadId: string, keepTranscript: boolean = true) => {
      if (!threadId || typeof threadId !== 'string') return;
      // Opening the thread we are already in is a no-op: no divider.
      if (threadId === currentThreadId) return;
      // One transition at a time: a double click or a duplicated event must
      // not stack dividers or race two resumes.
      if (pendingOpen || transition) return;

      pendingOpen = true;
      try {
        // Without thread resume the /thread/:id route is read-only anyway,
        // so a kept transcript could never be followed by a live chat.
        if (!threadResumable) {
          navigate(`/thread/${threadId}`);
          return;
        }

        // Check availability before tearing anything down: an unknown,
        // foreign or deleted thread must leave the current chat intact —
        // handled entirely locally, so the global 401/error handlers
        // (login redirect, generic toast) never see this probe.
        const available = await isThreadAvailable(
          apiClient.buildEndpoint(`/project/thread/${threadId}`)
        );
        if (!available) {
          console.warn(
            `open_thread: thread "${threadId}" is not available, ignoring.`
          );
          toast.error(
            t('chat.messages.threadUnavailable', {
              defaultValue: 'This chat is not available'
            })
          );
          return;
        }

        // One commit, same as ChatProfileSwitchListener: the teardown, the
        // kept transcript and the navigation must not be split across
        // renders, or a render located on the old thread with the state
        // already cleared would resume the old thread over the transition.
        flushSync(() => {
          // A pending AskUserMessage/AskFileMessage dies with the chat that
          // asked it, exactly as on a profile switch.
          setAskUser(undefined);
          setCallFn(undefined);
          setLoading(false);
          setAttachments([]);
          setPersistentCommand(undefined);

          // Read through updaters so these are the values at this moment,
          // without subscribing the caller to every streamed token.
          let kept: IStep[] = [];
          setMessages((previous) => {
            kept = previous;
            return [];
          });
          let keptBoundaries: IChatBoundary[] = [];
          setBoundaries((previous) => {
            keptBoundaries = previous;
            return [];
          });

          if (keepTranscript && kept.length) {
            const excursion = {
              id: uuidv4(),
              messages: freezeStreaming(kept),
              boundaries: keptBoundaries
            };
            setKeptExcursions((previous) => [...previous, excursion]);
          } else if (!keepTranscript) {
            // A plain open blanks the screen like a click in the history
            // list; earlier excursions must not linger above it.
            setKeptExcursions([]);
            setCollapsedExcursions({});
          }

          setTransition({ threadId, keepTranscript });

          // From here on this is the regular open-from-history path:
          // AutoResumeThread on /thread/:id clears the session and resumes.
          navigate(`/thread/${threadId}`);
        });
      } finally {
        pendingOpen = false;
      }
    },
    [
      apiClient,
      navigate,
      t,
      currentThreadId,
      transition,
      threadResumable,
      setAskUser,
      setCallFn,
      setLoading,
      setMessages,
      setAttachments,
      setPersistentCommand,
      setBoundaries,
      setKeptExcursions,
      setCollapsedExcursions,
      setTransition
    ]
  );
};
