import { ChevronRight } from 'lucide-react';
// Deliberately the raw hook, for the reason spelled out in
// OpenParentThreadButton.tsx: the local Translator wrapper answers '...' for a
// key it has not loaded yet, which would defeat the defaultValue below.
import { useTranslation } from 'react-i18next';

import { useElementSidebar } from '@chainlit/react-client';

import { Button } from '@/components/ui/button';

/**
 * The way back to the element panel, next to the thumb.
 *
 * It used to be a mute glyph standing in for an empty left slot, and it
 * opened the *left* thread-history sidebar. Both are gone: the panel is
 * session state now, so it can be hidden without being destroyed — and a
 * hidden panel needs somewhere to be asked back from. That is here, on both
 * layouts and always, because a control that appears only when there is
 * something to show teaches nobody it exists. With nothing in the panel it
 * opens an empty one, which is a legal state and a truthful answer.
 */
export default function ComposerChevron() {
  const { dispatch } = useElementSidebar();
  const { t } = useTranslation();

  return (
    // The paperclip's button exactly -- ghost, `size="icon"`, the same hover
    // fill. A bare <button> of its own box was 4px off the paperclip's centre
    // and lit nothing on hover, so it read as a glyph, not a control.
    <Button
      id="composer-chevron"
      type="button"
      variant="ghost"
      size="icon"
      onClick={() => dispatch({ op: 'show' })}
      aria-label={t('chat.input.actions.openSidePanel', {
        defaultValue: 'Open the side panel'
      })}
      className="hover:bg-muted"
    >
      <ChevronRight className="!size-6" />
    </Button>
  );
}
