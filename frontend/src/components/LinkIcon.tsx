import { cn } from '@/lib/utils';
import { useContext } from 'react';

import { ChainlitContext } from '@chainlit/react-client';

import { useTheme } from '@/components/ThemeProvider';

interface LinkIconProps {
  iconUrl?: string | null;
  iconUrlLight?: string | null;
  iconUrlDark?: string | null;
  iconMask?: boolean;
  className?: string;
  alt?: string;
}

export default function LinkIcon({
  iconUrl,
  iconUrlLight,
  iconUrlDark,
  iconMask,
  className,
  alt
}: LinkIconProps) {
  const apiClient = useContext(ChainlitContext);
  const { variant } = useTheme();

  const themedUrl =
    (variant === 'dark' ? iconUrlDark : iconUrlLight) ?? iconUrl;

  if (!themedUrl) return null;

  const resolvedUrl = themedUrl.startsWith('/public')
    ? apiClient.buildEndpoint(themedUrl)
    : themedUrl;

  if (iconMask) {
    return (
      <span
        aria-hidden="true"
        className={cn('shrink-0', className)}
        style={{
          backgroundColor: 'currentColor',
          maskImage: `url(${resolvedUrl})`,
          WebkitMaskImage: `url(${resolvedUrl})`,
          maskSize: 'contain',
          WebkitMaskSize: 'contain',
          maskRepeat: 'no-repeat',
          WebkitMaskRepeat: 'no-repeat',
          maskPosition: 'center',
          WebkitMaskPosition: 'center'
        }}
      />
    );
  }

  return <img src={resolvedUrl} className={className} alt={alt ?? ''} />;
}
