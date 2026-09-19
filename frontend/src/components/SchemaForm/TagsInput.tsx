import { useState } from 'react';

import { useTranslation } from '@/components/i18n/Translator';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';

interface Props {
  id: string;
  value: string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
}

/**
 * A `list[str]` as chips with a draft line under them.
 *
 * The draft is component state rather than a form field: nothing on the wire
 * corresponds to half a tag, and registering it would put it in the submitted
 * object.
 */
const TagsInput = ({ id, value, disabled, onChange }: Props) => {
  const [draft, setDraft] = useState('');
  const { t } = useTranslation();

  const commit = () => {
    const tag = draft.trim();
    if (!tag || value.includes(tag)) {
      setDraft('');
      return;
    }
    onChange([...value, tag]);
    setDraft('');
  };

  return (
    <div className="flex flex-col gap-2">
      {value.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {value.map((tag, index) => (
            <Badge
              key={`${tag}-${index}`}
              variant="secondary"
              className="gap-1"
            >
              {tag}
              {disabled ? null : (
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() =>
                    onChange(value.filter((_, at) => at !== index))
                  }
                >
                  ×
                </button>
              )}
            </Badge>
          ))}
        </div>
      ) : null}
      <Input
        id={id}
        value={draft}
        disabled={disabled}
        placeholder={t('account.tags.placeholder')}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return;
          // Without this the Enter reaches the form and submits it: a single
          // text input in a form makes Enter an implicit submit.
          event.preventDefault();
          commit();
        }}
      />
    </div>
  );
};

export default TagsInput;
