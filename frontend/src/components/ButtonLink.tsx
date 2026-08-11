import { useApi } from '@chainlit/react-client';

import LinkIcon from '@/components/LinkIcon';
import { Loader } from '@/components/Loader';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

export interface ButtonLinkProps {
  name?: string;
  displayName?: string;
  iconUrl?: string;
  iconUrlLight?: string;
  iconUrlDark?: string;
  iconMask?: boolean;
  url: string;
  target?: '_blank' | '_self' | '_parent' | '_top';
  // Endpoint returning {"label": "..."} used as the button text. When set,
  // the label is fetched on mount and a click re-fetches it instead of
  // navigating; displayName is shown until the first response.
  labelUrl?: string;
  // Re-fetch the label every N seconds.
  labelRefreshInterval?: number;
}

export default function ButtonLink({
  name,
  displayName,
  iconUrl,
  iconUrlLight,
  iconUrlDark,
  iconMask,
  url,
  target,
  labelUrl,
  labelRefreshInterval
}: ButtonLinkProps) {
  const { data, mutate, isValidating } = useApi<{ label: string }>(
    labelUrl ?? null,
    {
      refreshInterval: labelRefreshInterval ? labelRefreshInterval * 1000 : 0
    }
  );

  const label = (labelUrl && data?.label) || displayName;
  // Visual feedback while the label is being (re-)fetched: spinner + dimmed text.
  const refreshing = !!labelUrl && isValidating;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size={label ? 'default' : 'icon'}
            className="text-muted-foreground hover:text-muted-foreground"
          >
            <a
              href={labelUrl ? undefined : url}
              target={labelUrl ? undefined : (target ?? '_blank')}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 cursor-pointer"
              onClick={
                labelUrl
                  ? (e) => {
                      e.preventDefault();
                      mutate();
                    }
                  : undefined
              }
            >
              {refreshing ? (
                <Loader className="text-muted-foreground" />
              ) : (
                <LinkIcon
                  iconUrl={iconUrl}
                  iconUrlLight={iconUrlLight}
                  iconUrlDark={iconUrlDark}
                  iconMask={iconMask}
                  className="h-6 w-6"
                  alt={name}
                />
              )}
              {label && (
                <span className={refreshing ? 'opacity-50' : undefined}>
                  {label}
                </span>
              )}
            </a>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{name}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
