import { useContext, useEffect, useState } from 'react';
import { useSetRecoilState } from 'recoil';

import {
  ChainlitContext,
  useChatInteract,
  useChatMessages,
  useChatSession,
  useConfig
} from '@chainlit/react-client';

import { Markdown } from '@/components/Markdown';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger
} from '@/components/ui/hover-card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select';

import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { IAttachment, attachmentsState } from '@/state/chat';

import { NewChatDialog } from './NewChat';

interface Props {
  navigate?: (to: string) => void;
}

export default function ChatProfiles({ navigate }: Props) {
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const {
    chatProfile,
    setChatProfile,
    switchChatProfile,
    hotSwapChatProfile,
    session
  } = useChatSession();
  // Whether a swap can actually happen right now. The warning dialog and the
  // teardown must agree on this: deciding the dialog by the flag but the
  // teardown by socket liveness meant a blip silently destroyed the chat.
  const canHotSwap = !!hotSwapChatProfile && !!session?.socket?.connected;
  const { firstInteraction } = useChatMessages();
  const { clear } = useChatInteract();
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const resetKeptTranscript = useResetKeptTranscript();
  const [newChatProfile, setNewChatProfile] = useState<string | null>(null);
  const [openDialog, setOpenDialog] = useState(false);

  // Early return check to prevent unnecessary renders and resource waste
  if (!config?.chatProfiles?.length || config.chatProfiles.length <= 1) {
    return null;
  }

  // Handle case when no profile is selected
  useEffect(() => {
    // On the hot-swap path the server is the authority and announces the
    // profile with chat_profile_changed on every connect. Writing the atom
    // locally here would be the client/server divergence the design rules
    // out — and, since the atom drives the config refetch, a loop.
    if (!chatProfile && !hotSwapChatProfile) {
      setChatProfile(config.chatProfiles[0].name);
    }
  }, [chatProfile, config.chatProfiles, setChatProfile, hotSwapChatProfile]);

  // Handle case when selected profile becomes invalid
  useEffect(() => {
    if (chatProfile) {
      const profileExists = config.chatProfiles.some(
        (profile) => profile.name === chatProfile
      );
      if (!profileExists && !hotSwapChatProfile) {
        setChatProfile(config.chatProfiles[0].name);
      }
    }
  }, [chatProfile, config.chatProfiles, setChatProfile, hotSwapChatProfile]);

  const handleClose = () => {
    setOpenDialog(false);
    setNewChatProfile(null);
    navigate?.('/');
  };

  const handleConfirm = (profile: string) => {
    // Hot swap: same session, same thread, transcript kept — so none of the
    // teardown below applies. The atom is left to chat_profile_changed.
    // Falls back to the legacy path when the socket is dead.
    if (canHotSwap && switchChatProfile(profile)) {
      setNewChatProfile(null);
      setOpenDialog(false);
      return;
    }
    setChatProfile(profile);
    setNewChatProfile(null);
    setAttachments([]);
    // A manual profile change blanks the screen; transcripts kept by
    // returns to a parent thread would otherwise linger above it.
    resetKeptTranscript();
    clear();
    handleClose();
  };

  const allowHtml = config?.features?.unsafe_allow_html;
  const latex = config?.features?.latex;

  return (
    <div className="relative">
      <Select
        value={chatProfile || ''}
        onValueChange={(value) => {
          setNewChatProfile(value);
          // The warning dialog exists because the legacy path destroys the
          // chat; a hot swap keeps it, so there is nothing to warn about.
          if (firstInteraction && !canHotSwap) {
            setOpenDialog(true);
          } else {
            handleConfirm(value);
          }
        }}
      >
        <SelectTrigger
          id="chat-profiles"
          className="w-fit border-none bg-transparent text-muted-foreground font-semibold text-lg hover:bg-accent"
        >
          <SelectValue placeholder="Select profile" />
        </SelectTrigger>
        <SelectContent>
          {config.chatProfiles.map((profile) => {
            const icon = profile.icon?.includes('/public')
              ? apiClient.buildEndpoint(profile.icon)
              : profile.icon;

            return (
              <HoverCard openDelay={0} closeDelay={0} key={profile.name}>
                <HoverCardTrigger asChild>
                  <SelectItem
                    data-test={`select-item:${profile.name}`}
                    value={profile.name}
                    className="cursor-pointer"
                  >
                    <div className="flex items-center gap-2">
                      {icon && (
                        <img
                          src={icon}
                          alt={profile.display_name || profile.name}
                          className="w-6 h-6 rounded-md object-cover"
                        />
                      )}
                      <span>{profile.display_name || profile.name}</span>
                    </div>
                  </SelectItem>
                </HoverCardTrigger>
                <HoverCardContent
                  side="right"
                  id="chat-profile-description"
                  align="start"
                  className="w-80 overflow-visible"
                  sideOffset={10}
                >
                  <Markdown
                    allowHtml={allowHtml}
                    latex={latex}
                    renderMarkdown={true}
                  >
                    {profile.markdown_description}
                  </Markdown>
                </HoverCardContent>
              </HoverCard>
            );
          })}
        </SelectContent>
      </Select>
      <NewChatDialog
        open={openDialog}
        handleClose={handleClose}
        handleConfirm={() => newChatProfile && handleConfirm(newChatProfile)}
      />
    </div>
  );
}
