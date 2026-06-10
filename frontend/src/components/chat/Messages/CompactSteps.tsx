import { cn } from '@/lib/utils';
import { MessageContext } from 'contexts/MessageContext';
import { memo, useContext, useMemo, useState } from 'react';

import type { IAction, IMessageElement, IStep } from '@chainlit/react-client';

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger
} from '@/components/ui/accordion';
import { Translator } from 'components/i18n';

import { Messages } from '.';
import { MessageAvatar } from './Message/Avatar';

interface Props {
  steps: IStep[];
  elements: IMessageElement[];
  actions: IAction[];
  indent: number;
  isRunning?: boolean;
  scorableRun?: IStep;
}

const CompactSteps = memo(
  ({ steps, elements, actions, indent, isRunning, scorableRun }: Props) => {
    const { cot } = useContext(MessageContext);
    const [openValue, setOpenValue] = useState<string>('');

    // Filter steps based on cot mode (same logic as Message/index.tsx)
    const visibleSteps = useMemo(() => {
      return steps.filter((s) => {
        const isStep = !s.type.includes('message');
        if (!isStep) return false;
        if (cot === 'hidden') return false;
        if (cot === 'tool_call' && s.type !== 'tool') return false;
        return true;
      });
    }, [steps, cot]);

    // Show "Using" label while the run is still active (prevents flashing
    // the finished label between sequential tool calls)
    const showUsing = !!isRunning;

    // Get the last visible step name for the "Using X" label
    const lastStep = visibleSteps[visibleSteps.length - 1];
    const lastStepName = lastStep?.name || '';

    // Determine the label translation key for the finished state
    const usedLabelPath =
      cot === 'tool_call'
        ? 'chat.messages.status.usedTools'
        : 'chat.messages.status.usedSteps';

    const accordionId = 'compact-steps';

    return (
      <div className="step py-2">
        <div className="flex flex-grow pb-2">
          <div className="ai-message flex gap-4 w-full">
            <MessageAvatar author={lastStep?.name || 'Assistant'} />
            <div className="flex flex-col flex-grow w-0">
              <Accordion
                type="single"
                collapsible
                value={openValue}
                onValueChange={(val) => setOpenValue(val)}
                className="w-full"
              >
                <AccordionItem value={accordionId} className="border-none">
                  <AccordionTrigger
                    className={cn(
                      'flex items-center gap-1 justify-start transition-none p-0 hover:no-underline',
                      !showUsing &&
                        'text-muted-foreground hover:text-foreground',
                      showUsing && 'loading-shimmer'
                    )}
                  >
                    {showUsing ? (
                      <>
                        <Translator path="chat.messages.status.using" />{' '}
                        {lastStepName}
                      </>
                    ) : (
                      <Translator
                        path={usedLabelPath}
                        options={{ count: visibleSteps.length }}
                      />
                    )}
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="flex-grow mt-4 ml-1 pl-4 border-l-2 border-primary">
                      <Messages
                        messages={visibleSteps}
                        elements={elements}
                        actions={actions}
                        indent={indent + 1}
                        isRunning={isRunning}
                        scorableRun={scorableRun}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </div>
          </div>
        </div>
      </div>
    );
  }
);

CompactSteps.displayName = 'CompactSteps';

export { CompactSteps };
