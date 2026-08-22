import { prepareContent } from '@/lib/message';
import { isEqual } from 'lodash';
import { forwardRef, memo, useMemo } from 'react';

import type { IMessageElement, IStep } from '@chainlit/react-client';

import { CURSOR_PLACEHOLDER } from '@/components/BlinkingCursor';
import { Markdown } from '@/components/Markdown';

import { useWaitDisplayText } from '@/hooks/useWaitDisplayText';

import { InlinedElements } from './InlinedElements';

type ContentSection = 'input' | 'output';

export interface Props {
  elements: IMessageElement[];
  message: IStep;
  allowHtml?: boolean;
  latex?: boolean;
  renderMarkdown?: boolean;
  sections?: ContentSection[];
  /** The message is in wait mode: shimmer the output text and rotate `message.wait.texts`. */
  waitActive?: boolean;
}

const getMessageRenderProps = (message: IStep) => ({
  id: message.id,
  output: message.output,
  input: message.input,
  language: message.language,
  streaming: message.streaming,
  showInput: message.showInput,
  type: message.type,
  wait: message.wait
});

const MessageContent = memo(
  forwardRef<HTMLDivElement, Props>(
    (
      {
        message,
        elements,
        allowHtml,
        latex,
        renderMarkdown,
        sections,
        waitActive
      },
      ref
    ) => {
      // Rotation text for wait mode. Display-only: the persistent output is
      // untouched, so deactivation falls back to it.
      const waitText = useWaitDisplayText(
        waitActive ? message.wait : undefined
      );
      const displayedOutput =
        waitActive && waitText !== undefined ? waitText : message.output;

      const outputContent =
        message.streaming && displayedOutput
          ? displayedOutput + CURSOR_PLACEHOLDER
          : displayedOutput;

      const {
        preparedContent: output,
        inlinedElements: outputInlinedElements,
        refElements: outputRefElements
      } = prepareContent({
        elements,
        id: message.id,
        content: outputContent,
        language: message.language
      });

      const selectedSections = sections ?? ['input', 'output'];
      const sectionsSet = useMemo(
        () => new Set(selectedSections),
        [selectedSections]
      );

      const displayInput =
        sectionsSet.has('input') && message.input && message.showInput;
      const displayOutput = sectionsSet.has('output');

      const isMessage = message.type.includes('message');

      const outputMarkdownBody = (
        <Markdown
          allowHtml={allowHtml}
          latex={latex}
          renderMarkdown={renderMarkdown}
          refElements={outputRefElements}
        >
          {output}
        </Markdown>
      );

      const outputMarkdown = displayOutput ? (
        <>
          {!isMessage && displayInput && message.output ? (
            <div className="font-medium">Output</div>
          ) : null}
          {waitActive ? (
            // Shimmer only the text (elements/actions render normally); no
            // animation under prefers-reduced-motion, rotation still runs.
            <div className="motion-safe:animate-pulse">
              {outputMarkdownBody}
            </div>
          ) : (
            outputMarkdownBody
          )}
        </>
      ) : null;

      let inputMarkdown;

      if (displayInput) {
        const inputContent =
          message.streaming && message.input
            ? message.input + CURSOR_PLACEHOLDER
            : message.input;
        const { preparedContent: input, refElements: inputRefElements } =
          prepareContent({
            elements,
            id: message.id,
            content: inputContent,
            language:
              typeof message.showInput === 'string'
                ? message.showInput
                : undefined
          });

        inputMarkdown = (
          <>
            <Markdown
              allowHtml={allowHtml}
              latex={latex}
              renderMarkdown={renderMarkdown}
              refElements={inputRefElements}
            >
              {input}
            </Markdown>
          </>
        );
      }

      const markdownContent = (
        <div className="flex flex-col gap-4">
          {inputMarkdown}
          {outputMarkdown}
        </div>
      );

      return (
        <div ref={ref} className="message-content w-full flex flex-col gap-2">
          {displayInput || (displayOutput && output) ? markdownContent : null}
          {displayOutput ? (
            <InlinedElements elements={outputInlinedElements} />
          ) : null}
        </div>
      );
    }
  ),
  (prevProps, nextProps) => {
    return (
      prevProps.allowHtml === nextProps.allowHtml &&
      prevProps.latex === nextProps.latex &&
      prevProps.renderMarkdown === nextProps.renderMarkdown &&
      prevProps.elements === nextProps.elements &&
      prevProps.waitActive === nextProps.waitActive &&
      isEqual(
        prevProps.sections ?? ['input', 'output'],
        nextProps.sections ?? ['input', 'output']
      ) &&
      isEqual(
        getMessageRenderProps(prevProps.message),
        getMessageRenderProps(nextProps.message)
      )
    );
  }
);

export { MessageContent };
