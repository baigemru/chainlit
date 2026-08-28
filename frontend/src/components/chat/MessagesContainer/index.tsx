import { MessageContext } from '@/contexts/MessageContext';
import { getActiveWaitStepId } from '@/lib/waitMessage';
import {
  Fragment,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef
} from 'react';
import { useRecoilValue, useSetRecoilState } from 'recoil';
import { toast } from 'sonner';

import {
  ChainlitContext,
  IFeedback,
  IMessageElement,
  IStep,
  messagesState,
  sessionIdState,
  sideViewState,
  threadIdToResumeState,
  updateMessageById,
  useChatData,
  useChatInteract,
  useChatMessages,
  useConfig
} from '@chainlit/react-client';

import { Messages } from '@/components/chat/Messages';
import { useTranslation } from 'components/i18n/Translator';

import {
  chatBoundariesState,
  collapsedExcursionsState,
  keptExcursionsState
} from '@/state/chat';

import ChatBoundaryDivider from './ChatBoundaryDivider';
import CollapsedTranscript from './CollapsedTranscript';
import { buildTranscriptView } from './transcript';

interface Props {
  navigate?: (to: string) => void;
}

const MessagesContainer = ({ navigate }: Props) => {
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const { elements, askUser, loading, actions } = useChatData();
  const { messages } = useChatMessages();
  const { uploadFile: _uploadFile } = useChatInteract();
  const setMessages = useSetRecoilState(messagesState);
  const setSideView = useSetRecoilState(sideViewState);
  const sessionId = useRecoilValue(sessionIdState);
  const boundaries = useRecoilValue(chatBoundariesState);
  const excursions = useRecoilValue(keptExcursionsState);
  const collapsedExcursions = useRecoilValue(collapsedExcursionsState);
  const setCollapsedExcursions = useSetRecoilState(collapsedExcursionsState);

  const { t } = useTranslation();

  const uploadFile = useCallback(
    (file: File, onProgress: (progress: number) => void, parentId?: string) => {
      return _uploadFile(file, onProgress, parentId);
    },
    [_uploadFile]
  );

  const onFeedbackUpdated = useCallback(
    async (message: IStep, onSuccess: () => void, feedback: IFeedback) => {
      toast.promise(apiClient.setFeedback(feedback, sessionId), {
        loading: t('chat.messages.feedback.status.updating'),
        success: (res) => {
          setMessages((prev) =>
            updateMessageById(prev, message.id, {
              ...message,
              feedback: {
                ...feedback,
                id: res.feedbackId
              }
            })
          );
          onSuccess();
          return t('chat.messages.feedback.status.updated');
        },
        error: (err) => {
          return <span>{err.message}</span>;
        }
      });
    },
    []
  );

  const onFeedbackDeleted = useCallback(
    async (message: IStep, onSuccess: () => void, feedbackId: string) => {
      toast.promise(apiClient.deleteFeedback(feedbackId), {
        loading: t('chat.messages.feedback.status.updating'),
        success: () => {
          setMessages((prev) =>
            updateMessageById(prev, message.id, {
              ...message,
              feedback: undefined
            })
          );
          onSuccess();
          return t('chat.messages.feedback.status.updated');
        },
        error: (err) => {
          return <span>{err.message}</span>;
        }
      });
    },
    []
  );

  const knownSideElementsRef = useRef<Map<string, IMessageElement>>(new Map());
  const knownSideOrderRef = useRef<string[]>([]);

  useEffect(() => {
    const sideElements = elements.filter((e) => e.display === 'side');

    if (sideElements.length === 0) {
      // Only clear a side view this effect put there. The sidebar is also
      // driven straight from the socket (ElementSidebar.set_elements), and
      // when those events arrive before this component mounts, clearing
      // unconditionally would wipe a sidebar that has no message elements
      // behind it at all.
      const ownedBySideElements = knownSideOrderRef.current.length > 0;
      knownSideElementsRef.current = new Map();
      knownSideOrderRef.current = [];
      if (ownedBySideElements) setSideView(undefined);
      return;
    }

    const prevMap = knownSideElementsRef.current;
    const prevOrder = knownSideOrderRef.current;
    const currentIds = sideElements.map((e) => e.id);

    const hasChanged =
      currentIds.length !== prevOrder.length ||
      currentIds.some((id, i) => prevOrder[i] !== id) ||
      sideElements.some((e) => prevMap.get(e.id) !== e);

    if (hasChanged) {
      const newMap = new Map<string, IMessageElement>();
      sideElements.forEach((e) => newMap.set(e.id, e));
      knownSideElementsRef.current = newMap;
      knownSideOrderRef.current = currentIds;
      setSideView({
        title: sideElements[sideElements.length - 1].name,
        elements: sideElements
      });
    }
  }, [elements]);

  const onElementRefClick = useCallback(
    (element: IMessageElement) => {
      if (
        element.display === 'side' ||
        (element.display === 'page' && !navigate)
      ) {
        setSideView({ title: element.name, elements: [element] });
        return;
      }

      let path = `/element/${element.id}`;

      if (element.threadId) {
        path += `?thread=${element.threadId}`;
      }

      return navigate?.(element.display === 'page' ? path : '#');
    },
    [setSideView, navigate]
  );

  const onError = useCallback((error: string) => toast.error(error), [toast]);

  const enableFeedback = !!config?.dataPersistence;

  // Drives the wait-message presentation: only the conversation's very last
  // step may shimmer, so any newer step deactivates the previous one. This is
  // undefined unless the last step actually carries `wait`, so for apps that
  // never send wait messages the memoized context below stays stable.
  const activeWaitStepId = useMemo(
    () => getActiveWaitStepId(messages),
    [messages]
  );

  // Memoize the context object since it's created on each render.
  // This prevents unnecessary re-renders of children components when no props have changed.
  const memoizedContext = useMemo(() => {
    return {
      uploadFile,
      askUser,
      allowHtml: config?.features?.unsafe_allow_html,
      latex: config?.features?.latex,
      renderUserMarkdown: config?.features?.user_message_markdown,
      loading,
      showFeedbackButtons: enableFeedback,
      uiName: config?.ui?.name || '',
      cot: config?.ui?.cot || 'hidden',
      cotDisplay: config?.ui?.cot_display || 'list',
      showStepDetails: config?.ui?.show_step_details ?? true,
      activeWaitStepId,
      onElementRefClick,
      onError,
      onFeedbackUpdated,
      onFeedbackDeleted
    };
  }, [
    askUser,
    enableFeedback,
    loading,
    activeWaitStepId,
    config?.ui?.name,
    config?.ui?.cot,
    config?.ui?.cot_display,
    config?.ui?.show_step_details,
    config?.features?.unsafe_allow_html,
    config?.features?.user_message_markdown,
    onElementRefClick,
    onError,
    onFeedbackUpdated
  ]);

  const sections = useMemo(
    () => buildTranscriptView(excursions, messages, boundaries),
    [excursions, messages, boundaries]
  );

  const toggleExcursion = useCallback(
    (excursionId: string) => {
      setCollapsedExcursions((prev) => ({
        ...prev,
        [excursionId]: !prev[excursionId]
      }));
    },
    [setCollapsedExcursions]
  );

  // Boundaries describe the live transcript only. Opening a thread from the
  // history replays persisted steps, and one of them can carry the id a
  // boundary points at — which would draw a divider inside a conversation
  // that never had a profile switch. A soft switch clears idToResume, so it
  // is not affected.
  const idToResume = useRecoilValue(threadIdToResumeState);
  const setBoundaries = useSetRecoilState(chatBoundariesState);
  useEffect(() => {
    if (idToResume) setBoundaries([]);
  }, [idToResume, setBoundaries]);

  // Bring a freshly drawn divider into view once. Autoscroll pins the last
  // user message to the top, and that message belongs to the new chat, so
  // without this the line the user is meant to notice starts off-screen.
  // Only fires while the new chat is still empty, leaving autoscroll to take
  // over as soon as it produces anything. Excursions count too: a return to
  // the parent thread draws its divider without touching the boundaries.
  const dividerCount = boundaries.length + excursions.length;
  const boundaryCountRef = useRef(dividerCount);
  useEffect(() => {
    const added = dividerCount > boundaryCountRef.current;
    boundaryCountRef.current = dividerCount;
    if (!added) return;

    requestAnimationFrame(() => {
      const dividers = document.querySelectorAll('.chat-boundary');
      dividers[dividers.length - 1]?.scrollIntoView({ block: 'start' });
    });
  }, [dividerCount]);

  // Transcripts of chats that already ended are read-only, and must not react
  // to the current chat's activity: Messages recomputes a run's "is running"
  // from this context and ignores the prop, so without `loading: false` an
  // unfinished run kept on screen blinks whenever the new chat generates.
  const endedChatContext = useMemo(
    () => ({
      ...memoizedContext,
      loading: false,
      // A kept transcript can contain a copy of the step that is currently
      // in wait mode in the live conversation; ended sections never shimmer.
      activeWaitStepId: undefined,
      // Feedback posts against the live session, which never saw these
      // steps — the server would reject it after the UI already showed it
      // as accepted.
      showFeedbackButtons: false
    }),
    [memoizedContext]
  );

  return (
    <MessageContext.Provider value={memoizedContext}>
      {sections.map((section, index) => {
        const isCurrent = index === sections.length - 1;
        // A fully deduplicated segment has nothing to fold away: no toggle,
        // no "0 messages" strip.
        const collapsible =
          !!section.excursionId && section.messages.length > 0;
        const isCollapsed =
          collapsible && !!collapsedExcursions[section.excursionId!];
        const body = isCollapsed ? (
          <CollapsedTranscript
            count={section.messages.length}
            onExpand={() => toggleExcursion(section.excursionId!)}
          />
        ) : (
          <Messages
            indent={0}
            isRunning={loading && isCurrent}
            messages={section.messages}
            elements={elements}
            actions={actions}
          />
        );

        return (
          <Fragment key={section.key}>
            {isCurrent ? (
              body
            ) : (
              <MessageContext.Provider value={endedChatContext}>
                {body}
              </MessageContext.Provider>
            )}
            {section.excursionId ? (
              <ChatBoundaryDivider
                isReturn
                collapsed={isCollapsed}
                onToggleCollapse={
                  collapsible
                    ? () => toggleExcursion(section.excursionId!)
                    : undefined
                }
              />
            ) : section.startedProfile !== undefined ? (
              <ChatBoundaryDivider profile={section.startedProfile} />
            ) : null}
          </Fragment>
        );
      })}
    </MessageContext.Provider>
  );
};

export default MessagesContainer;
