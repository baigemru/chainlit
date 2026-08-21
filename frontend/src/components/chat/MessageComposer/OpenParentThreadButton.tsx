import { CornerLeftUp } from 'lucide-react';
// Deliberately the raw hook: the local Translator wrapper returns '...' for a
// missing key before t() runs, which would defeat the defaultValue below and
// break every locale that has not been translated yet.
import { useTranslation } from 'react-i18next';
import { useSetRecoilState } from 'recoil';

import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { useParentThreadId } from '@/hooks/useParentThread';

import { openThreadRequestState } from '@/state/chat';

/**
 * Returns to the thread the current chat was spawned from by a profile
 * switch. Only rendered when the parent is known, so in a chat without one
 * (and in the copilot widget, where nothing ever learns a parent) it does
 * not exist. Never disabled while visible: like `set_chat_profile`, the
 * return may interrupt a running generation. The click stays router-free —
 * ThreadReturnListener picks the request up and performs the navigation.
 */
export default function OpenParentThreadButton() {
  const parentThreadId = useParentThreadId();
  const setRequest = useSetRecoilState(openThreadRequestState);
  const { t } = useTranslation();

  if (!parentThreadId) return null;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            id="open-parent-thread"
            data-test="open-parent-thread"
            onClick={() =>
              setRequest({
                threadId: parentThreadId,
                keepTranscript: true
              })
            }
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
