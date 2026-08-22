// Deliberately the raw hook: the local Translator wrapper returns '...' for a
// missing key before t() runs, which would defeat the defaultValue below and
// break every locale that has not been translated yet.
import { useTranslation } from 'react-i18next';

interface Props {
  /** Root messages hidden by the collapse. */
  count: number;
  onExpand: () => void;
}

/**
 * The compact strip a collapsed excursion segment leaves behind: the child
 * chat's messages folded into one line, clickable to bring them back.
 */
export default function CollapsedTranscript({ count, onExpand }: Props) {
  const { t } = useTranslation();

  return (
    <button
      type="button"
      data-test="collapsed-transcript"
      onClick={onExpand}
      className="my-2 w-full rounded-md border border-dashed py-2 text-xs text-muted-foreground hover:bg-muted"
    >
      {t('chat.messages.collapsedChildMessages', {
        defaultValue: '{{count}} child chat messages',
        count
      })}
      {' · '}
      {t('chat.messages.expandTranscript', { defaultValue: 'Expand' })}
    </button>
  );
}
