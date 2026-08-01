import { useContext } from 'react';

import { ChainlitContext } from '@chainlit/react-client';

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
  iconMask?: boolean;
  url: string;
  target?: '_blank' | '_self' | '_parent' | '_top';
}

export default function ButtonLink({
  name,
  displayName,
  iconUrl,
  iconMask,
  url,
  target
}: ButtonLinkProps) {
  const apiClient = useContext(ChainlitContext);

  const resolvedIconUrl = iconUrl?.startsWith('/public')
    ? apiClient.buildEndpoint(iconUrl)
    : iconUrl;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size={displayName ? 'default' : 'icon'}
            className="text-muted-foreground hover:text-muted-foreground"
          >
            <a
              href={url}
              target={target ?? '_blank'}
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1"
            >
              {resolvedIconUrl ? (
                iconMask ? (
                  <span
                    aria-hidden="true"
                    className="h-6 w-6 shrink-0"
                    style={{
                      backgroundColor: 'currentColor',
                      maskImage: `url(${resolvedIconUrl})`,
                      WebkitMaskImage: `url(${resolvedIconUrl})`,
                      maskSize: 'contain',
                      WebkitMaskSize: 'contain',
                      maskRepeat: 'no-repeat',
                      WebkitMaskRepeat: 'no-repeat',
                      maskPosition: 'center',
                      WebkitMaskPosition: 'center'
                    }}
                  />
                ) : (
                  <img src={resolvedIconUrl} className={'h-6 w-6'} alt={name} />
                )
              ) : null}
              {displayName && <span>{displayName}</span>}
            </a>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{name}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
