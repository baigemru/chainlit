import { useEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useSetRecoilState } from 'recoil';

import type { SessionHandoff } from '@chainlit/react-client';
import {
  IStep,
  askUserState,
  loadingState,
  messagesState,
  useChatInteract,
  useChatSession,
  useChatTransport,
  useConfig
} from '@chainlit/react-client';

import { freezeStreaming } from '@/components/chat/MessagesContainer/transcript';

import {
  IAttachment,
  IChatBoundary,
  attachmentsState,
  chatBoundariesState,
  collapsedExcursionsState,
  keptExcursionsState
} from '@/state/chat';

export default function ChatProfileSwitchListener() {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { chatProfile } = useChatSession();
  const transport = useChatTransport();
  const { clear } = useChatInteract();
  const setAskUser = useSetRecoilState(askUserState);
  const setLoading = useSetRecoilState(loadingState);
  const setMessages = useSetRecoilState(messagesState);
  const setBoundaries = useSetRecoilState(chatBoundariesState);
  const setKeptExcursions = useSetRecoilState(keptExcursionsState);
  const setCollapsedExcursions = useSetRecoilState(collapsedExcursionsState);
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);

  // Latest switch logic lives in a ref so the socket subscription below is
  // only re-registered when the socket itself changes.
  const switchRef = useRef<(payload: SessionHandoff) => void>();
  switchRef.current = (payload) => {
    const { chatProfile: name, hasTransitMessage, nextSessionId } = payload;
    const keepTranscript = !!payload.keepTranscript;

    if (!config?.chatProfiles?.some((profile) => profile.name === name)) {
      console.warn(
        `session.handoff: unknown chat profile "${name}", ignoring.`
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
    // update share a lane; here we run in a socket callback, where the
    // Recoil writes are scheduled at sync priority while the router update
    // is not. That split commits a render still located on /thread/<old>
    // but with the thread id already cleared, which makes Thread mount
    // AutoResumeThread and resume the previous thread over the new chat.
    flushSync(() => {
      setAskUser(undefined);
      setLoading(false);
      setAttachments([]);

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
      // The successor is stated in full rather than assembled: the id the
      // backend parked the hand-off record under (absent only when there was
      // nothing to hand over, and a random one is then exactly right) and
      // the profile it switches to are one decision, and the connect effect
      // must never see half of it.
      clear({ sessionId: nextSessionId || undefined, chatProfile: name });

      if (keepTranscript && kept === undefined) {
        console.error(
          'session.handoff: could not read the transcript; keeping the chat cleared.'
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

  // Subscribed to the transport, not to a socket: the listener outlives
  // every connection the transport builds, so nothing re-registers it.
  useEffect(
    () =>
      transport.onMessage((message) => {
        if (message.t === 'session.handoff') switchRef.current?.(message);
      }),
    [transport]
  );

  return null;
}
