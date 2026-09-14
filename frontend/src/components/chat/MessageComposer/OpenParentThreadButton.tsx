import { CornerLeftUp } from 'lucide-react';
// Deliberately the raw hook: the local Translator wrapper returns '...' for a
// missing key before t() runs, which would defeat the defaultValue below and
// break every locale that has not been translated yet.
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { useOpenThread } from '@/hooks/useOpenThread';
import { useParentThreadId } from '@/hooks/useParentThread';

/**
 * Returns to the thread the current chat was spawned from by a profile
 * switch. Only rendered when the parent is known, so a chat without one does
 * not have it. Never disabled while visible: like `set_chat_profile`, the
 * return may interrupt a running generation.
 *
 * The click opens the thread itself. It used to park a request in an atom
 * for ThreadReturnListener to execute, because the composer also rendered
 * in an embedder that had no router and therefore no `useOpenThread`. That
 * embedder is gone, and the detour would now be a hop through global state
 * to reach a hook this component can call directly.
 *
 * Two components, because `useOpenThread` reaches for the router and the API
 * client: an early return cannot skip a hook, so calling it out here would
 * make every composer in the app -- in a chat with no parent, which is most
 * of them -- depend on both. The hook is paid for only where the button is
 * actually on screen.
 */
export default function OpenParentThreadButton() {
  const parentThreadId = useParentThreadId();

  if (!parentThreadId) return null;
  return <ReturnButton parentThreadId={parentThreadId} />;
}

function ReturnButton({ parentThreadId }: { parentThreadId: string }) {
  const openThread = useOpenThread();
  const { t } = useTranslation();

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            id="open-parent-thread"
            data-test="open-parent-thread"
            onClick={() => void openThread(parentThreadId, true)}
            className="hover:bg-muted rounded-full"
            variant="ghost"
            size="icon"
          >
            <CornerLeftUp className="!size-6" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          <p>
            {t('chat.input.actions.openParentThread', {
              defaultValue: 'Back to the original chat'
            })}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
