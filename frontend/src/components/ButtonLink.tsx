import LinkIcon from '@/components/LinkIcon';
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
}

export default function ButtonLink({
  name,
  displayName,
  iconUrl,
  iconUrlLight,
  iconUrlDark,
  iconMask,
  url,
  target
}: ButtonLinkProps) {
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
              <LinkIcon
                iconUrl={iconUrl}
                iconUrlLight={iconUrlLight}
                iconUrlDark={iconUrlDark}
                iconMask={iconMask}
                className="h-6 w-6"
                alt={name}
              />
              {displayName && <span>{displayName}</span>}
            </a>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{name}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
