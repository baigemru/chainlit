import { MessageContext } from 'contexts/MessageContext';
import React, { memo, useContext, useMemo } from 'react';

import {
  type IAction,
  type IMessageElement,
  type IStep
} from '@chainlit/react-client';

import BlinkingCursor from '@/components/BlinkingCursor';

import { CompactSteps } from './CompactSteps';
import { Message } from './Message';

interface Props {
  messages: IStep[];
  elements: IMessageElement[];
  actions: IAction[];
  indent: number;
  isRunning?: boolean;
  scorableRun?: IStep;
}

const CL_RUN_NAMES = ['on_chat_start', 'on_message', 'on_audio_end'];

const hasActiveToolStep = (step: IStep): boolean => {
  return (
    step.steps?.some(
      (s) =>
        (s.type === 'tool' && s.start && !s.end && !s.isError) ||
        s.type.includes('message') ||
        hasActiveToolStep(s)
    ) || false
  );
};

const hasAssistantMessage = (step: IStep): boolean => {
  return (
    step.steps?.some(
      (s) => s.type === 'assistant_message' || hasAssistantMessage(s)
    ) || false
  );
};

const countVisibleSteps = (steps: IStep[], cot: string): number => {
  let count = 0;
  for (const s of steps) {
    if (!s.type.includes('message')) {
      if (cot !== 'tool_call' || s.type === 'tool') count++;
    }
    if (s.steps) count += countVisibleSteps(s.steps, cot);
  }
  return count;
};

interface StepSegment {
  isPassthrough: boolean;
  items: IStep[];
}

// Messages and chainlit runs must never be swallowed into a CompactSteps
// block: a run wraps the whole turn (including its assistant messages), so
// collapsing it would hide the answer. Both render through Messages as-is.
const isPassthroughStep = (s: IStep): boolean =>
  s.type.includes('message') || CL_RUN_NAMES.includes(s.name);

// Group consecutive top-level children by passthrough/step type, preserving
// order, so compact mode only collapses runs of adjacent steps instead of
// merging every step in the run into a single block up front.
const segmentSteps = (steps: IStep[]): StepSegment[] => {
  const segments: StepSegment[] = [];
  for (const s of steps) {
    const isPassthrough = isPassthroughStep(s);
    const last = segments[segments.length - 1];
    if (last && last.isPassthrough === isPassthrough) {
      last.items.push(s);
    } else {
      segments.push({ isPassthrough, items: [s] });
    }
  }
  return segments;
};

const Messages = memo(
  ({ messages, elements, actions, indent, isRunning, scorableRun }: Props) => {
    const messageContext = useContext(MessageContext);

    const lastAssistantMessage = useMemo(() => {
      return messages.findLast((m) => m.type === 'assistant_message');
    }, [messages]);

    const lastScorableAssistantMessage = useMemo(() => {
      return scorableRun?.steps?.findLast(
        (m) => m.type === 'assistant_message'
      );
    }, [scorableRun]);

    return (
      <>
        {messages.map((m) => {
          // Handle chainlit runs
          if (CL_RUN_NAMES.includes(m.name)) {
            const isRunning = !m.end && !m.isError && messageContext.loading;
            const isToolCallCoT =
              messageContext.cot === 'tool_call' ||
              messageContext.cot === 'full';
            const isHiddenCoT = messageContext.cot === 'hidden';

            const showToolCoTLoader = isToolCallCoT
              ? isRunning && !hasActiveToolStep(m)
              : false;

            const showHiddenCoTLoader = isHiddenCoT
              ? isRunning && !hasAssistantMessage(m)
              : false;
            // Ignore on_chat_start for scorable run
            const scorableRun =
              !isRunning && m.name !== 'on_chat_start' ? m : undefined;

            // Determine if compact mode should be used
            const useCompact =
              messageContext.cotDisplay === 'compact' &&
              messageContext.cot !== 'hidden';

            return (
              <React.Fragment key={m.id}>
                {m.steps?.length ? (
                  useCompact ? (
                    <SegmentedMessages
                      steps={m.steps}
                      elements={elements}
                      actions={actions}
                      indent={indent}
                      isRunning={isRunning}
                      scorableRun={scorableRun}
                    />
                  ) : (
                    <Messages
                      messages={m.steps}
                      elements={elements}
                      actions={actions}
                      indent={indent}
                      isRunning={isRunning}
                      scorableRun={scorableRun}
                    />
                  )
                ) : null}
                {(showToolCoTLoader || showHiddenCoTLoader) &&
                m.name !== 'on_chat_start' ? (
                  <BlinkingCursor />
                ) : null}
              </React.Fragment>
            );
          } else {
            // Score the current run
            const _scorableRun = m.type === 'run' ? m : scorableRun;
            // The message is scorable if it is the last assistant message of the run

            const isRunLastAssistantMessage =
              m.type === 'run' ? false : m === lastScorableAssistantMessage;

            const isLastAssistantMessage = m === lastAssistantMessage;

            const isScorable =
              isRunLastAssistantMessage || isLastAssistantMessage;

            return (
              <Message
                message={m}
                elements={elements}
                actions={actions}
                key={m.id}
                indent={indent}
                isRunning={isRunning}
                scorableRun={_scorableRun}
                isScorable={isScorable}
              />
            );
          }
        })}
      </>
    );
  }
);

interface SegmentedProps {
  steps: IStep[];
  elements: IMessageElement[];
  actions: IAction[];
  indent: number;
  isRunning?: boolean;
  scorableRun?: IStep;
}

// Renders a list of sibling steps in compact mode: consecutive step-only
// runs collapse into a single CompactSteps block, message segments render
// as-is. Used for run children and for steps nested under a message.
const SegmentedMessages = memo(
  ({
    steps,
    elements,
    actions,
    indent,
    isRunning,
    scorableRun
  }: SegmentedProps) => {
    const messageContext = useContext(MessageContext);
    const segments = useMemo(() => segmentSteps(steps), [steps]);

    return (
      <>
        {segments.map((segment, i) => {
          // A segment is only still "running" if nothing follows it yet — an
          // earlier step-only run followed by a later segment has completed.
          const segmentIsRunning = isRunning && i === segments.length - 1;

          if (!segment.isPassthrough) {
            const segmentStepCount = countVisibleSteps(
              segment.items,
              messageContext.cot
            );
            if (segmentStepCount > 1) {
              return (
                <CompactSteps
                  key={i}
                  steps={segment.items}
                  elements={elements}
                  actions={actions}
                  indent={indent}
                  isRunning={segmentIsRunning}
                  scorableRun={scorableRun}
                />
              );
            }
          }
          return (
            <Messages
              key={i}
              messages={segment.items}
              elements={elements}
              actions={actions}
              indent={indent}
              isRunning={segmentIsRunning}
              scorableRun={scorableRun}
            />
          );
        })}
      </>
    );
  }
);

export { Messages, SegmentedMessages };
