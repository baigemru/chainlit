import { useEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useSetRecoilState } from 'recoil';

import {
  IStep,
  askUserState,
  callFnState,
  loadingState,
  messagesState,
  sessionIdState,
  useChatInteract,
  useChatSession,
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
  persistentCommandState
} from '@/state/chat';

interface SetChatProfilePayload {
  name: string;
  keepTranscript?: boolean;
  /** Set when the backend parked a transit message for the next session. */
  hasTransitMessage?: boolean;
  /** Id the backend parked the hand-off record under; adopt it verbatim. */
  nextSessionId?: string | null;
}

export default function ChatProfileSwitchListener() {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { session, chatProfile, setChatProfile } = useChatSession();
  const { clear } = useChatInteract();
  const setAskUser = useSetRecoilState(askUserState);
  const setCallFn = useSetRecoilState(callFnState);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setSessionId = useSetRecoilState(sessionIdState);
  const setBoundaries = useSetRecoilState(chatBoundariesState);
  const setKeptExcursions = useSetRecoilState(keptExcursionsState);
  const setCollapsedExcursions = useSetRecoilState(collapsedExcursionsState);
  const setPersistentCommand = useSetRecoilState(persistentCommandState);
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);

  // Latest switch logic lives in a ref so the socket subscription below is
  // only re-registered when the socket itself changes.
  const switchRef = useRef<(payload: SetChatProfilePayload) => void>();
  switchRef.current = (payload) => {
    const { name, hasTransitMessage, nextSessionId } = payload || {};
    const keepTranscript = !!payload?.keepTranscript;

    if (!config?.chatProfiles?.some((profile) => profile.name === name)) {
      console.warn(
        `set_chat_profile: unknown chat profile "${name}", ignoring.`
      );
      return;
    }

    const alreadyActive = chatProfile === name;

    // Keeping the transcript is never a no-op: it draws a line and starts a
    // new thread, which is meaningful even within the same profile. So is a
    // parked transit message — leaving it unclaimed would strand it.
    if (alreadyActive && !keepTranscript && !hasTransitMessage) return;

    // Same path as a manual selection (ChatProfiles.handleConfirm), minus
    // the confirmation dialog: the server already made the decision.
    //
    // flushSync keeps the whole teardown in ONE commit. A manual selection
    // runs in a discrete React event, so its state updates and the router
    // update share a lane; here we run in a socket.io callback, where the
    // Recoil writes are scheduled at sync priority while the router update
    // is not. That split commits a render still located on /thread/<old>
    // but with the thread id already cleared, which makes Thread mount
    // AutoResumeThread and resume the previous thread over the new chat.
    flushSync(() => {
      setAskUser(undefined);
      setCallFn(undefined);
      setLoading(false);
      setChatProfile(name);
      setAttachments([]);
      setPersistentCommand(undefined);

      // Read through updaters so these are the values before clear() wipes
      // them, without subscribing this component to every streamed token.
      let kept: IStep[] | undefined;
      let keptBoundaries: IChatBoundary[] = [];
      if (keepTranscript) {
        setMessages((previous) => {
          kept = freezeStreaming(previous);
          return previous;
        });
        setBoundaries((previous) => {
          keptBoundaries = previous;
          return previous;
        });
      }

      // The real teardown, so this path inherits whatever it grows upstream.
      clear();

      // clear() resets the session id to a random one; overwrite it with
      // the id the backend parked the hand-off record under. Recoil applies
      // these set calls in order, so the last write wins, and flushSync
      // commits once. Absent only when there was nothing to hand over — the
      // random id from clear() is then exactly right.
      if (nextSessionId) {
        setSessionId(nextSessionId);
      }

      if (keepTranscript && kept === undefined) {
        console.error(
          'set_chat_profile: could not read the transcript; keeping the chat cleared.'
        );
      }

      const afterMessageId = kept?.at(-1)?.id;
      if (kept?.length && afterMessageId) {
        setMessages(kept);
        // A boundary already on that message would be overwritten by the new
        // one, silently dropping the divider it drew.
        setBoundaries([
          ...keptBoundaries.filter((b) => b.afterMessageId !== afterMessageId),
          { afterMessageId, profile: name }
        ]);
      } else {
        setBoundaries([]);
      }

      // A hard switch blanks the screen; excursions kept by earlier returns
      // to a parent thread would otherwise linger above the fresh chat. A
      // soft switch keeps everything on screen, excursions included.
      if (!keepTranscript) {
        setKeptExcursions([]);
        setCollapsedExcursions({});
      }

      navigate('/');
    });
  };

  useEffect(() => {
    const socket = session?.socket;
    if (!socket) return;
    const handler = (payload: SetChatProfilePayload) =>
      switchRef.current?.(payload);
    socket.on('set_chat_profile', handler);
    return () => {
      socket.off('set_chat_profile', handler);
    };
  }, [session?.socket]);

  return null;
}
