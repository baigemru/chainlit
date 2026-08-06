import { useContext } from 'react';
// Deliberately the raw hook: the local Translator wrapper returns '...' for a
// missing key before t() runs, which would defeat the defaultValue below and
// break every locale that has not been translated yet.
import { useTranslation } from 'react-i18next';

import { ChainlitContext, useConfig } from '@chainlit/react-client';

import { Separator } from '@/components/ui/separator';

interface Props {
  /** Profile the chat below this divider started on. */
  profile?: string;
}

export default function ChatBoundaryDivider({ profile }: Props) {
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const { t } = useTranslation();

  const chatProfile = config?.chatProfiles?.find((p) => p.name === profile);
  const name = chatProfile?.display_name || chatProfile?.name || profile;
  const icon = chatProfile?.icon?.includes('/public')
    ? apiClient.buildEndpoint(chatProfile.icon)
    : chatProfile?.icon;

  const label = name
    ? t('chat.messages.newChatIn', {
        defaultValue: 'New chat · {{profile}}',
        profile: name
      })
    : t('chat.messages.newChat', { defaultValue: 'New chat' });

  return (
    <div
      className="chat-boundary flex items-center gap-3 py-6"
      role="separator"
      aria-label={label}
    >
      <Separator className="w-auto flex-1" />
      <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
        {icon ? (
          <img src={icon} alt="" className="size-4 rounded object-cover" />
        ) : null}
        <span>{label}</span>
      </div>
      <Separator className="w-auto flex-1" />
    </div>
  );
}
