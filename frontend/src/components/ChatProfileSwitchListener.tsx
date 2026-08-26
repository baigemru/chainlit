import { useEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useSetRecoilState } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

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
    const { name, hasTransitMessage } = payload || {};
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

    // The backend parked a transit record under the emitting session's id —
    // the transit message and/or the current thread's id, which the new
    // thread stores as its parentThreadId. Re-key it to the session we are
    // about to open; claim on EVERY switch, since the parent link rides
    // along even without a message. Emitted synchronously inside this
    // socket callback on purpose: socket.io only delivers events while
    // connected, so a synchronous emit is guaranteed to hit the live socket
    // (and its write buffer is flushed before the disconnect below closes
    // the transport). Deferring this to an effect or timer would risk
    // queueing it on a socket that never reconnects. It also runs after
    // every early return above — claiming for a switch that is not going to
    // happen would strand the record on a session id nobody will ever
    // connect with (the no-op return leaves a parent-only record to expire
    // by TTL, which is harmless).
    const nextSessionId = uuidv4();
    session?.socket.emit('claim_transit_message', {
      sessionId: nextSessionId
    });

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

      // clear() resets the session id to a random one; overwrite it with the
      // id the transit record was claimed for. Recoil applies these set
      // calls in order, so the last write wins, and flushSync commits once.
      setSessionId(nextSessionId);

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
