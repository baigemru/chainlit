import { cn } from '@/lib/utils';

import {
  useChatData,
  useChatInteract,
  useChatMessages
} from '@chainlit/react-client';

import { Send } from '@/components/icons/Send';
import { Stop } from '@/components/icons/Stop';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';
import { Translator } from 'components/i18n';

interface SubmitButtonProps {
  /**
   * Whether a click here would send something: the composer accepts *and*
   * there is something in it. One prop, because it answers both questions
   * this button asks — whether Send is live, and whether this is Send at
   * all.
   *
   * The second one only became a question when an application gained a way
   * to declare a run background: work is then running *and* the composer
   * accepts, so "stop that" and "send this" are both things the click might
   * mean. Empty box → Stop, there is nothing else it could do. Anything
   * typed → Send, because they typed it to send it and Enter sends it too,
   * so a Stop sitting there would disagree with the key beside it. Stop
   * stays one clear box away.
   */
  canSend?: boolean;
  onSubmit: () => void;
  /**
   * Sizing from the composer, which needs a bigger tap target in its mobile
   * pill. It lands on both buttons: send and stop swap in place, and a stop
   * that resized under the thumb would be a moving target.
   */
  className?: string;
}

export default function SubmitButton({
  canSend,
  onSubmit,
  className
}: SubmitButtonProps) {
  const { loading } = useChatData();
  const { firstInteraction } = useChatMessages();
  const { stopTask } = useChatInteract();

  return (
    <TooltipProvider>
      {loading && firstInteraction && !canSend ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              id="stop-button"
              onClick={stopTask}
              size="icon"
              className={cn('rounded-full h-8 w-8', className)}
            >
              <Stop className="!size-6" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>
              <Translator path="chat.input.actions.stop" />
            </p>
          </TooltipContent>
        </Tooltip>
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              id="chat-submit"
              disabled={!canSend}
              onClick={onSubmit}
              size="icon"
              className={cn('rounded-full h-8 w-8', className)}
            >
              <Send className="!size-6" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>
              <Translator path="chat.input.actions.send" />
            </p>
          </TooltipContent>
        </Tooltip>
      )}
    </TooltipProvider>
  );
}
