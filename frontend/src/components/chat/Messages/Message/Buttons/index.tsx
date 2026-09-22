import {
  IAction,
  type IStep,
  useChatMessages,
  useConfig
} from '@chainlit/react-client';

import CopyButton from '@/components/CopyButton';

import MessageActions from './Actions';
import { DebugButton } from './DebugButton';
import { FeedbackButtons } from './FeedbackButtons';

interface Props {
  message: IStep;
  actions: IAction[];
  run?: IStep;
  contentRef?: React.RefObject<HTMLDivElement>;
}

const MessageButtons = ({ message, actions, run, contentRef }: Props) => {
  const { config } = useConfig();
  const { firstInteraction } = useChatMessages();

  const isUser = message.type === 'user_message';
  const isAsk = message.waitForAnswer;
  const hasContent = !!message.output;
  const showCopyButton = !!run && hasContent && !isUser && !isAsk;

  const messageActions = actions.filter((a) => a.forId === message.id);

  const showDebugButton =
    !!config?.debugUrl && !!message.threadId && !!firstInteraction && !!run;

  const show = showCopyButton || showDebugButton || messageActions?.length;

  if (!show || message.streaming) {
    return null;
  }

  return (
    <div className="-ml-1.5 flex items-center flex-wrap max-w-full">
      {showCopyButton ? (
        <CopyButton content={message.output} contentRef={contentRef} />
      ) : null}
      {run ? <FeedbackButtons message={run} /> : null}
      {/*
        The labelled actions are the ones that overflow a phone; the icon
        buttons around them stay a row, or every icon takes a line of its
        own. How they are laid out at each width is `MessageActions`' own
        decision -- it is the only thing here that knows a chip from a
        command.
      */}
      {messageActions.length ? (
        <MessageActions actions={messageActions} />
      ) : null}
      {showDebugButton ? (
        <DebugButton debugUrl={config.debugUrl!} step={message} />
      ) : null}
    </div>
  );
};

export { MessageButtons };
