import { cn } from '@/lib/utils';
import { MessageContext } from 'contexts/MessageContext';
import { useContext, useMemo } from 'react';

import { type IAction } from '@chainlit/react-client';

import Icon from '@/components/Icon';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { useIsMobile } from '@/hooks/use-mobile';

const AskActionButton = ({
  action,
  isMobile
}: {
  action: IAction;
  isMobile: boolean;
}) => {
  const { loading, askUser } = useContext(MessageContext);

  const content = useMemo(() => {
    return action.icon
      ? action.label
      : action.label
        ? action.label
        : action.name;
  }, [action]);

  const icon = useMemo(() => {
    if (action.icon) return <Icon name={action.icon as any} />;
    return null;
  }, [action]);

  const button = (
    <Button
      className={cn(
        'h-auto min-h-10',
        // A narrow screen gets one button per row, clipped to the row: a
        // wrapped label pushed the button past the width of the message.
        isMobile
          ? 'w-full min-w-0 justify-start'
          : 'break-words whitespace-normal'
      )}
      id={action.id}
      onClick={() => {
        askUser?.callback(action);
      }}
      variant="outline"
      disabled={loading || askUser?.awaitingReply}
      title={isMobile ? content : undefined}
    >
      {icon}
      {isMobile ? <span className="truncate">{content}</span> : content}
    </Button>
  );

  if (action.tooltip) {
    return (
      <TooltipProvider delayDuration={100}>
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent>
            <p>{action.tooltip}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  } else {
    return button;
  }
};

const AskActionButtons = ({
  messageId,
  actions
}: {
  messageId: string;
  actions: IAction[];
}) => {
  const { askUser } = useContext(MessageContext);
  const isMobile = useIsMobile();

  const belongsToMessage = askUser?.spec.stepId === messageId;
  const isAskingAction = askUser?.spec.type === 'action';
  const keys = askUser?.spec.keys;
  const filteredActions = useMemo(() => {
    const offered = actions.filter(
      (a) => a.forId === messageId && keys?.includes(a.id)
    );
    // Stacked, the first button is the only one on screen without a scroll,
    // and the ask declares its confirming action first. Arrival order of the
    // action list is not that order by construction.
    return offered.sort(
      (a, b) => (keys?.indexOf(a.id) ?? 0) - (keys?.indexOf(b.id) ?? 0)
    );
  }, [actions, messageId, keys]);

  if (!belongsToMessage || !isAskingAction || !actions.length) return null;

  return (
    <div
      className={cn(
        'flex gap-1',
        isMobile ? 'flex-col items-stretch w-full' : 'items-center flex-wrap'
      )}
    >
      {filteredActions.map((a) => (
        <AskActionButton key={a.id} action={a} isMobile={isMobile} />
      ))}
    </div>
  );
};

export { AskActionButtons };
