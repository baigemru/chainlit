import { cn } from '@/lib/utils';

import { useConfig } from '@chainlit/react-client';

import { Markdown } from '@/components/Markdown';

interface Props {
  children: string;
  className?: string;
}

/**
 * The line under the composer: what pressing Enter will do, or what the
 * conversation is busy with. One component because two screens draw it — the
 * welcome screen, from the profile's `composer_hint` or the server's
 * `composer.state`, and the chat's footer, from the server alone — and a
 * register kept in two copies is how one of them drifts.
 *
 * Markdown because the applications that want one want a link or a bold
 * number in it.
 */
export default function ComposerHint({ children, className }: Props) {
  const { config } = useConfig();

  return (
    <div className={cn('composer-hint max-w-full', className)}>
      {/* The same register as the watermark: small, muted, one paragraph
          with no margin of its own. */}
      <Markdown
        allowHtml={config?.features?.unsafe_allow_html}
        latex={config?.features?.latex}
        renderMarkdown={true}
        className="text-xs text-muted-foreground text-center [&_p]:m-0 [&_div]:mt-0 [&_div]:leading-snug"
      >
        {children}
      </Markdown>
    </div>
  );
}
