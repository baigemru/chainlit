import { cn } from '@/lib/utils';
import { ChevronRight } from 'lucide-react';
// Deliberately the raw hook, for the reason spelled out in
// OpenParentThreadButton.tsx: the local Translator wrapper answers '...' for a
// key it has not loaded yet, which would defeat the defaultValue below.
import { useTranslation } from 'react-i18next';

import { useSidebar } from '@/components/ui/sidebar';

import { useHasLeftSidebar } from '@/hooks/useHasLeftSidebar';

// The same 32px box in both forms: whether the glyph does anything must not
// move the pill's geometry.
const BOX =
  'flex h-8 w-8 flex-none items-center justify-center text-muted-foreground';

/**
 * The pill's left slot when both of its buttons render null. With uploads off
 * and no parent thread a Telegram-shaped pill whose text starts flush at the
 * rounded edge reads as a defect; a mute ">" — the oldest prompt glyph there
 * is — holds the slot instead.
 *
 * Where a thread history exists the glyph is also the thumb's way into it. The
 * header keeps its own trigger, at the top of the screen; this is the same
 * action at the bottom, where the hand already is.
 *
 * Two components, because `useSidebar` throws outside a provider
 * (`ui/sidebar.tsx`) and the decorative form has to stay renderable wherever
 * the composer is — a bare composer in a test among them.
 */
export default function ComposerChevron() {
  const hasLeftSidebar = useHasLeftSidebar();

  if (hasLeftSidebar) return <SidebarChevron />;

  return (
    <span id="composer-chevron" aria-hidden="true" className={BOX}>
      <ChevronRight className="!size-6" />
    </span>
  );
}

function SidebarChevron() {
  const { toggleSidebar } = useSidebar();
  const { t } = useTranslation();

  return (
    <button
      id="composer-chevron"
      type="button"
      onClick={toggleSidebar}
      aria-label={t('threadHistory.sidebar.actions.open', {
        defaultValue: 'Open sidebar'
      })}
      className={cn(BOX, 'rounded-full hover:text-foreground')}
    >
      <ChevronRight className="!size-6" />
    </button>
  );
}
