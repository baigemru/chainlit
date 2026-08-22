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
  /**
   * Marks the divider drawn by a return to the parent thread. It keeps the
   * regular divider look but labels the return and carries the collapse
   * toggle for the child-chat segment right above it.
   */
  isReturn?: boolean;
  /** Whether the segment above this (return) divider is collapsed. */
  collapsed?: boolean;
  /** Collapse/expand the segment above this (return) divider. */
  onToggleCollapse?: () => void;
}

export default function ChatBoundaryDivider({
  profile,
  isReturn,
  collapsed,
  onToggleCollapse
}: Props) {
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const { t } = useTranslation();

  const chatProfile = config?.chatProfiles?.find((p) => p.name === profile);
  const name = chatProfile?.display_name || chatProfile?.name || profile;
  const icon = chatProfile?.icon?.includes('/public')
    ? apiClient.buildEndpoint(chatProfile.icon)
    : chatProfile?.icon;

  const label = isReturn
    ? t('chat.messages.backToParent', {
        defaultValue: 'Returned to the original chat'
      })
    : name
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
        {isReturn && onToggleCollapse ? (
          <button
            type="button"
            data-test="collapse-transcript"
            onClick={onToggleCollapse}
            className="underline underline-offset-2 hover:text-foreground"
          >
            {collapsed
              ? t('chat.messages.expandTranscript', {
                  defaultValue: 'Expand'
                })
              : t('chat.messages.collapseTranscript', {
                  defaultValue: 'Collapse'
                })}
          </button>
        ) : null}
      </div>
      <Separator className="w-auto flex-1" />
    </div>
  );
}
