import { KeyRound } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useConfig } from '@chainlit/react-client';

import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';
import { Translator } from 'components/i18n';

interface Props {
  /** Render as a row of the header's overflow menu instead of a button. */
  collapsed?: boolean;
}

export default function ApiKeys({ collapsed }: Props) {
  const { config } = useConfig();
  const requiredKeys = !!config?.userEnv?.length;

  if (!requiredKeys) return null;

  if (collapsed) {
    return (
      <DropdownMenuItem asChild>
        <Link id="api-keys-button" to="/env">
          <Translator path="navigation.user.menu.apiKeys" />
          <KeyRound className="ml-auto size-4" />
        </Link>
      </DropdownMenuItem>
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Link to="/env">
            <Button
              id="api-keys-button"
              size="icon"
              variant="ghost"
              className="text-muted-foreground hover:text-muted-foreground"
            >
              <KeyRound className="!size-4" />
            </Button>
          </Link>
        </TooltipTrigger>
        <TooltipContent>
          <p>
            <Translator path="navigation.user.menu.apiKeys" />
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
