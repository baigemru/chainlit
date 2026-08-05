import { useEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useRecoilState, useSetRecoilState } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

import {
  IStep,
  askUserState,
  useAuth,
  useChatData,
  useChatInteract,
  useChatSession,
  useConfig
} from '@chainlit/react-client';

import {
  IAttachment,
  attachmentsState,
  pendingFirstMessageState
} from '@/state/chat';

interface SetChatProfilePayload {
  name: string;
  startNew?: boolean;
  firstMessage?: string | null;
}

// On a fresh connection the server emits an initial `task_end`
// (connection_successful) and, when an on_chat_start callback exists,
// schedules it right away — its `task_start` follows on the same socket.
// If no `task_start` shows up within this grace period after the initial
// `task_end`, there is no on_chat_start to wait for.
const CHAT_START_GRACE_MS = 500;

// A pending message whose delivery never happened (dead socket, on_chat_start
// that never returned) must not resurface in a later, unrelated conversation.
const PENDING_MESSAGE_TTL_MS = 60000;

// A switch carrying the message we just delivered is an echo of that delivery
// (the app re-matched its own trigger), not a new request. Honouring it would
// loop: deliver -> on_message -> switch -> deliver.
const LOOP_GUARD_MS = 5000;

export default function ChatProfileSwitchListener() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { config } = useConfig();
  const { session, chatProfile, setChatProfile } = useChatSession();
  const { clear, sendMessage, replyMessage } = useChatInteract();
  const { askUser, connected } = useChatData();
  const setAskUser = useSetRecoilState(askUserState);
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const [pendingFirstMessage, setPendingFirstMessage] = useRecoilState(
    pendingFirstMessageState
  );

  // Last message handed to the backend, used to break the delivery -> trigger
  // -> switch -> delivery loop described above.
  const lastDeliveredRef = useRef<{ text: string; at: number }>();

  const buildUserMessage = (output: string): IStep => ({
    threadId: '',
    id: uuidv4(),
    name: user?.identifier || 'User',
    type: 'user_message',
    output,
    createdAt: new Date().toISOString(),
    metadata: { location: window.location.href }
  });

  // Returns the pending message if it is still fresh, and clears it either way.
  const takePending = (): string | undefined => {
    if (!pendingFirstMessage) return undefined;
    setPendingFirstMessage(undefined);
    if (Date.now() - pendingFirstMessage.createdAt > PENDING_MESSAGE_TTL_MS) {
      console.warn('set_chat_profile: dropping a stale pending first message.');
      return undefined;
    }
    return pendingFirstMessage.text;
  };

  const markDelivered = (text: string) => {
    lastDeliveredRef.current = { text, at: Date.now() };
  };

  // Latest switch logic lives in a ref so the socket subscription below is
  // only re-registered when the socket itself changes.
  const switchRef = useRef<(payload: SetChatProfilePayload) => void>();
  switchRef.current = (payload) => {
    const { name, startNew = true, firstMessage } = payload || {};

    if (!config?.chatProfiles?.some((profile) => profile.name === name)) {
      console.warn(
        `set_chat_profile: unknown chat profile "${name}", ignoring.`
      );
      return;
    }

    const lastDelivered = lastDeliveredRef.current;
    if (
      firstMessage &&
      lastDelivered?.text === firstMessage &&
      Date.now() - lastDelivered.at < LOOP_GUARD_MS
    ) {
      console.warn(
        'set_chat_profile: ignoring a switch triggered by the message just delivered.'
      );
      return;
    }

    const alreadyActive = chatProfile === name;

    if (!startNew) {
      // Only move the selector, leave the current chat untouched. Note this
      // does not change the profile of the running server session.
      if (firstMessage) {
        console.warn(
          'set_chat_profile: firstMessage is ignored when startNew is false.'
        );
      }
      if (!alreadyActive) setChatProfile(name);
      return;
    }

    if (alreadyActive && !firstMessage) return;

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
      setPendingFirstMessage(
        firstMessage ? { text: firstMessage, createdAt: Date.now() } : undefined
      );
      setAskUser(undefined);
      setChatProfile(name);
      setAttachments([]);
      clear();
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

  // If the new profile's on_chat_start asks a text question, answer it with
  // the pending message. Other ask types (file/action/element) cannot be
  // answered with a text step — the backend would raise on the reply — so
  // those fall through to the regular delivery below.
  useEffect(() => {
    if (!pendingFirstMessage || !connected || !askUser) return;
    if (askUser.spec.type !== 'text') return;
    const text = takePending();
    if (!text) return;
    replyMessage(buildUserMessage(text));
    markDelivered(text);
  }, [pendingFirstMessage, connected, askUser, replyMessage]);

  const deliverRef = useRef<() => void>();
  deliverRef.current = () => {
    const text = takePending();
    if (!text) return;
    sendMessage(buildUserMessage(text));
    markDelivered(text);
  };

  // Otherwise send the pending message as regular user input, but only
  // after on_chat_start (if any) has finished, so the new profile has set
  // up its session first.
  useEffect(() => {
    const socket = session?.socket;
    if (!socket || !pendingFirstMessage) return;

    let ackReceived = false;
    let chatStartRunning = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const onTaskEnd = () => {
      if (!ackReceived) {
        ackReceived = true;
        timer = setTimeout(() => deliverRef.current?.(), CHAT_START_GRACE_MS);
      } else if (chatStartRunning) {
        chatStartRunning = false;
        deliverRef.current?.();
      }
    };
    const onTaskStart = () => {
      chatStartRunning = true;
      if (timer) clearTimeout(timer);
    };
    const onAsk = ({ spec }: { spec?: { type?: string } }) => {
      // Only a text ask consumes the pending message (see the effect above).
      if (spec?.type !== 'text') return;
      chatStartRunning = false;
      if (timer) clearTimeout(timer);
    };

    socket.on('task_end', onTaskEnd);
    socket.on('task_start', onTaskStart);
    socket.on('ask', onAsk);

    return () => {
      socket.off('task_end', onTaskEnd);
      socket.off('task_start', onTaskStart);
      socket.off('ask', onAsk);
      if (timer) clearTimeout(timer);
    };
  }, [session?.socket, pendingFirstMessage]);

  return null;
}
