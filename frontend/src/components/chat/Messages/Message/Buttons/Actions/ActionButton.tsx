import { cn } from '@/lib/utils';
import { MessageContext } from 'contexts/MessageContext';
import { useCallback, useContext, useMemo, useState } from 'react';
import { useRecoilValue } from 'recoil';
import { toast } from 'sonner';

import {
  type ActionVariant,
  ChainlitContext,
  type IAction,
  sessionIdState
} from '@chainlit/react-client';

import Icon from '@/components/Icon';
import { Loader } from '@/components/Loader';
import { Button, type ButtonProps } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { useIsMobile } from '@/hooks/use-mobile';

/**
 * A variant is a weight, not a colour: the application says how loudly a
 * button asks to be pressed and the theme decides what that looks like.
 */
const BUTTON_VARIANT: Record<ActionVariant, ButtonProps['variant']> = {
  default: 'ghost',
  primary: 'default',
  secondary: 'outline',
  chip: 'outline'
};

/**
 * Anything the shadcn variant does not already say. `cn` is tailwind-merge,
 * and the class list passed to `Button` wins over the one its variant
 * built -- which is why `primary` adds nothing: a `text-muted-foreground`
 * here would overwrite the `text-primary-foreground` that makes the
 * accented button readable. For the same reason `chip`'s `rounded-full`
 * beats the `rounded-md` of `size="sm"`.
 */
const VARIANT_CLASS: Record<ActionVariant, string> = {
  default: 'text-muted-foreground',
  primary: '',
  secondary: '',
  chip: 'h-7 rounded-full px-3 text-xs font-normal text-muted-foreground'
};

interface ActionProps {
  action: IAction;
}

const ActionButton = ({ action }: ActionProps) => {
  const { loading, askUser } = useContext(MessageContext);
  const isMobile = useIsMobile();
  const apiClient = useContext(ChainlitContext);
  const sessionId = useRecoilValue(sessionIdState);
  const [isRunning, setIsRunning] = useState(false);

  // The server omits the field at its default and an action built here
  // never names one, so absent is `default`. This is the only reader of
  // `variant`, which is why the default lands here and not in `toAction`.
  const variant: ActionVariant = action.variant ?? 'default';

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
    // Unlike the composer, an action button is not gated on `connected`: it
    // is on screen from the frame that created it. The session handle is
    // what says the session is really there -- it arrives with
    // `session.ready` and `clear()` drops it -- so a click before the
    // handshake, or in the gap a New Chat opens, addresses nothing rather
    // than posting `session_id=undefined` at whatever the server has.
    if (!sessionId) return;
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

  // A chip is a question the user may tap, laid out in its own wrapping
  // row; stretching it across half a phone would make it a button again.
  const stretched = isMobile && variant !== 'chip';

  const button = (
    <Button
      id={action.id}
      onClick={handleClick}
      size="sm"
      variant={BUTTON_VARIANT[variant]}
      className={cn(
        VARIANT_CLASS[variant],
        // Stacked on a narrow screen, so a long label is clipped to the
        // message width instead of reaching past it.
        stretched && 'w-full min-w-0 justify-start'
      )}
      disabled={loading || isRunning}
      title={isMobile ? content : undefined}
    >
      {icon}
      {stretched ? <span className="truncate">{content}</span> : content}
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
