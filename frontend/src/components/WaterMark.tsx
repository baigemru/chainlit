import { Markdown } from '@/components/Markdown';
import { useTranslation } from '@/components/i18n/Translator';

export default function WaterMark() {
  const { t } = useTranslation();
  const text = t('chat.watermark');

  return (
    <div
      className="watermark max-w-full overflow-hidden"
      // One line, always: at 375px the default wording wraps to two and
      // steals ~30px of permanent height from the composer. The full text
      // stays reachable through the title.
      title={text}
      style={{
        display: 'flex',
        alignItems: 'center',
        textDecoration: 'none'
      }}
    >
      {/* The renderer emits the paragraph as a div, so the one-line rules
          have to be hung on `div`, not on `p`. */}
      <Markdown className="min-w-0 [&_p]:m-0 [&_p]:leading-snug [&_div]:leading-snug [&_div]:mt-0 [&_div]:whitespace-nowrap [&_div]:overflow-hidden [&_div]:text-ellipsis [&_strong]:font-semibold text-xs text-muted-foreground">
        {text}
      </Markdown>
    </div>
  );
}
