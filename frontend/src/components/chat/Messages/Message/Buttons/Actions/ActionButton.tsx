import { cn } from '@/lib/utils';
import { MessageContext } from 'contexts/MessageContext';
import { useCallback, useContext, useMemo, useState } from 'react';
import { useRecoilValue } from 'recoil';
import { toast } from 'sonner';

import {
  ChainlitContext,
  type IAction,
  sessionIdState
} from '@chainlit/react-client';

import Icon from '@/components/Icon';
import { Loader } from '@/components/Loader';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { useIsMobile } from '@/hooks/use-mobile';

interface ActionProps {
  action: IAction;
}

const ActionButton = ({ action }: ActionProps) => {
  const { loading, askUser } = useContext(MessageContext);
  const isMobile = useIsMobile();
  const apiClient = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const [isRunning, setIsRunning] = useState(false);

  const content = useMemo(() => {
    return action.icon
      ? action.label
      : action.label
        ? action.label
        : action.name;
  }, [action]);

  const icon = useMemo(() => {
    if (isRunning) return <Loader />;
    if (action.icon) return <Icon name={action.icon as any} />;
    return null;
  }, [action, isRunning]);

  const handleClick = useCallback(async () => {
    try {
      setIsRunning(true);
      await apiClient.callAction(action, sessionId);
    } catch (err) {
      toast.error(String(err));
    } finally {
      setIsRunning(false);
    }
  }, [action, sessionId, apiClient]);

  const isAskingAction = askUser?.spec.type === 'action';
  const ignore = isAskingAction && askUser?.spec.keys?.includes(action.id);

  if (ignore) return null;

  const button = (
    <Button
      id={action.id}
      onClick={handleClick}
      size="sm"
      variant="ghost"
      className={cn(
        'text-muted-foreground',
        // Stacked on a narrow screen, so a long label is clipped to the
        // message width instead of reaching past it.
        isMobile && 'w-full min-w-0 justify-start'
      )}
      disabled={loading || isRunning}
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

export { ActionButton };
