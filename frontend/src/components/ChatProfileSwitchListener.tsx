import { useEffect, useRef } from 'react';
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

  const buildUserMessage = (output: string): IStep => ({
    threadId: '',
    id: uuidv4(),
    name: user?.identifier || 'User',
    type: 'user_message',
    output,
    createdAt: new Date().toISOString(),
    metadata: { location: window.location.href }
  });

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

    const alreadyActive = chatProfile === name;

    if (!startNew) {
      // Only move the selector, leave the current chat untouched.
      if (!alreadyActive) setChatProfile(name);
      return;
    }

    if (alreadyActive && !firstMessage) return;

    // Same path as a manual selection (ChatProfiles.handleConfirm), minus
    // the confirmation dialog: the server already made the decision.
    setPendingFirstMessage(firstMessage || undefined);
    setAskUser(undefined);
    setChatProfile(name);
    setAttachments([]);
    clear();
    navigate('/');
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

  // If the new profile's on_chat_start asks a question (AskUserMessage),
  // answer it with the pending message.
  useEffect(() => {
    if (!pendingFirstMessage || !connected || !askUser) return;
    replyMessage(buildUserMessage(pendingFirstMessage));
    setPendingFirstMessage(undefined);
  }, [pendingFirstMessage, connected, askUser, replyMessage]);

  const deliverRef = useRef<() => void>();
  deliverRef.current = () => {
    if (!pendingFirstMessage) return;
    sendMessage(buildUserMessage(pendingFirstMessage));
    setPendingFirstMessage(undefined);
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
    const onAsk = () => {
      // An ask is pending: the reply effect above handles delivery.
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
