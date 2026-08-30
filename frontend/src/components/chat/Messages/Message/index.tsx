import { cn } from '@/lib/utils';
import { isWaitActive } from '@/lib/waitMessage';
import { MessageContext } from 'contexts/MessageContext';
import { memo, useContext, useMemo, useRef } from 'react';

import {
  type IAction,
  type IMessageElement,
  type IStep
} from '@chainlit/react-client';

import { useIsMobile } from '@/hooks/use-mobile';
import { useLayoutMaxWidth } from 'hooks/useLayoutMaxWidth';

import { Messages, SegmentedMessages } from '..';
import { AskActionButtons } from './AskActionButtons';
import { AskFileButton } from './AskFileButton';
import { MessageAvatar } from './Avatar';
import { MessageButtons } from './Buttons';
import { MessageContent } from './Content';
import Step from './Step';
import UserMessage from './UserMessage';

interface Props {
  message: IStep;
  elements: IMessageElement[];
  actions: IAction[];
  indent: number;
  isRunning?: boolean;
  isScorable?: boolean;
  scorableRun?: IStep;
}

const EMPTY_ELEMENTS: IMessageElement[] = [];

const Message = memo(
  ({
    message,
    elements,
    actions,
    isRunning,
    indent,
    isScorable,
    scorableRun
  }: Props) => {
    const {
      activeWaitStepId,
      allowHtml,
      cot,
      cotDisplay,
      latex,
      renderUserMarkdown,
      onError
    } = useContext(MessageContext);
    const layoutMaxWidth = useLayoutMaxWidth();
    const isMobile = useIsMobile();
    const contentRef = useRef<HTMLDivElement>(null);
    const isUserMessage = message.type === 'user_message';
    const isStep = !message.type.includes('message');
    // Only keep tool calls if Chain of Thought is tool_call
    const toolCallSkip =
      isStep && cot === 'tool_call' && message.type !== 'tool';

    const hiddenSkip = isStep && cot === 'hidden';

    const skip = toolCallSkip || hiddenSkip;
    // Transient wait presentation (shimmer + text rotation): only while this
    // message is still the last step of the conversation.
    const waitActive = isWaitActive(message, activeWaitStepId);
    const showInputSection = Boolean(message.input && message.showInput);
    const shouldRenderOutput = !showInputSection || Boolean(message.output);

    const author = message.metadata?.avatarName || message.name;
    const avatar = (
      <MessageAvatar
        author={author}
        isError={message.isError}
        iconName={message.metadata?.icon}
        messageChatProfile={message.metadata?.chat_profile}
      />
    );

    const userMessageContent = useMemo(
      () => (
        <MessageContent
          elements={EMPTY_ELEMENTS}
          message={message}
          allowHtml={allowHtml}
          latex={latex}
          renderMarkdown={renderUserMarkdown}
        />
      ),
      [message, allowHtml, latex]
    );

    if (skip) {
      if (!message.steps) {
        return null;
      }
      return (
        <Messages
          messages={message.steps}
          elements={elements}
          actions={actions}
          indent={indent}
          isRunning={isRunning}
          scorableRun={scorableRun}
        />
      );
    }

    return (
      <>
        <div
          data-step-type={message.type}
          // The application's per-message override of the scroll anchor, read
          // by ScrollContainer. Undefined drops the attribute, which is what
          // "the config decides" looks like from there.
          data-anchor={message.metadata?.anchor}
          data-test={waitActive ? 'wait-message' : undefined}
          className="step py-2"
        >
          <div
            className="flex flex-col"
            style={{
              maxWidth: indent ? '100%' : layoutMaxWidth
            }}
          >
            <div
              className={cn('flex flex-grow pb-2')}
              id={`step-${message.id}`}
            >
              {/* User message is displayed differently */}
              {isUserMessage ? (
                <div className="flex flex-col flex-grow max-w-full">
                  <UserMessage message={message} elements={elements}>
                    {userMessageContent}
                  </UserMessage>
                </div>
              ) : (
                <div
                  className={cn(
                    'ai-message flex w-full',
                    // A column on a phone: the avatar column costs 15% of the
                    // width of every message, and product cards live in the rest.
                    isMobile ? 'flex-col gap-2' : 'gap-4'
                  )}
                >
                  {!isStep || !indent ? (
                    isMobile ? (
                      <div className="flex items-center gap-2 min-w-0">
                        {avatar}
                        <span className="text-sm font-medium text-muted-foreground truncate">
                          {author}
                        </span>
                      </div>
                    ) : (
                      avatar
                    )
                  ) : null}
                  {/* Display the step and its children */}
                  {isStep ? (
                    <Step step={message} isRunning={isRunning}>
                      {showInputSection ? (
                        <MessageContent
                          elements={elements}
                          message={message}
                          allowHtml={allowHtml}
                          latex={latex}
                          renderMarkdown={true}
                          sections={['input']}
                        />
                      ) : null}
                      {message.steps ? (
                        <Messages
                          messages={message.steps.filter(
                            (s) => !s.type.includes('message')
                          )}
                          elements={elements}
                          actions={actions}
                          indent={indent + 1}
                          isRunning={isRunning}
                        />
                      ) : null}
                      {shouldRenderOutput ? (
                        <MessageContent
                          ref={contentRef}
                          elements={elements}
                          message={message}
                          allowHtml={allowHtml}
                          latex={latex}
                          renderMarkdown={true}
                          sections={showInputSection ? ['output'] : undefined}
                        />
                      ) : null}
                      <MessageButtons
                        message={message}
                        actions={actions}
                        contentRef={contentRef}
                      />
                    </Step>
                  ) : (
                    // Display an assistant message
                    <div className="flex flex-col items-start min-w-[150px] flex-grow gap-2">
                      <MessageContent
                        ref={contentRef}
                        elements={elements}
                        message={message}
                        allowHtml={allowHtml}
                        latex={latex}
                        renderMarkdown={true}
                        waitActive={waitActive}
                      />

                      <AskFileButton messageId={message.id} onError={onError} />
                      <AskActionButtons
                        actions={actions}
                        messageId={message.id}
                      />

                      <MessageButtons
                        message={message}
                        actions={actions}
                        run={
                          scorableRun && isScorable ? scorableRun : undefined
                        }
                        contentRef={contentRef}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        {/* Make sure the child assistant messages of a step are displayed at the root level. */}
        {message.steps && isStep ? (
          <Messages
            messages={message.steps.filter((s) => s.type.includes('message'))}
            elements={elements}
            actions={actions}
            indent={0}
            isRunning={isRunning}
            scorableRun={scorableRun}
          />
        ) : null}
        {/* Display the child steps if the message is not a step (usually a user message). */}
        {message.steps && !isStep ? (
          cotDisplay === 'compact' && cot !== 'hidden' ? (
            // Compact mode: collapse consecutive step-only runs nested under
            // this message, same as for direct children of the run.
            <SegmentedMessages
              steps={message.steps}
              elements={elements}
              actions={actions}
              indent={indent}
              isRunning={isRunning}
            />
          ) : (
            <Messages
              messages={message.steps}
              elements={elements}
              actions={actions}
              indent={indent}
              isRunning={isRunning}
            />
          )
        ) : null}
      </>
    );
  }
);

export { Message };
