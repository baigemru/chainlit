import { useContext, useEffect, useMemo, useState } from 'react';
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

import {
  isListed,
  matchesDevice,
  pickDefaultProfile,
  useDeviceKey
} from '@/hooks/use-mobile';
import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { IAttachment, attachmentsState } from '@/state/chat';

import { NewChatDialog } from './NewChat';

interface Props {
  navigate?: (to: string) => void;
}

export default function ChatProfiles({ navigate }: Props) {
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const { chatProfile, setChatProfile } = useChatSession();
  const { firstInteraction } = useChatMessages();
  const { clear } = useChatInteract();
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const resetKeptTranscript = useResetKeptTranscript();
  const [newChatProfile, setNewChatProfile] = useState<string | null>(null);
  const [openDialog, setOpenDialog] = useState(false);
  const device = useDeviceKey();

  const profiles = config?.chatProfiles;

  // What this device is actually *offered*: listed, and meant for it. This
  // list alone decides whether there is a selector at all.
  const offeredProfiles = useMemo(
    () =>
      profiles?.filter(
        (profile) => isListed(profile) && matchesDevice(profile.device, device)
      ) ?? [],
    [profiles, device]
  );

  // What the open selector lists. A thread opened from history, or a door a
  // starter walked the user through, may well live in a profile this device
  // is never offered — it has to keep working, and the trigger has to keep
  // naming it, so the selected one stays in the list even though it never
  // counts towards the decision above.
  const visibleProfiles = useMemo(
    () =>
      profiles?.filter(
        (profile) =>
          (isListed(profile) && matchesDevice(profile.device, device)) ||
          profile.name === chatProfile
      ) ?? [],
    [profiles, device, chatProfile]
  );

  // Handle case when no profile is selected
  useEffect(() => {
    if (!chatProfile && profiles?.length) {
      setChatProfile(pickDefaultProfile(profiles, device));
    }
  }, [chatProfile, profiles, device, setChatProfile]);

  // Handle case when selected profile becomes invalid. Checked against the
  // full list on purpose: a profile hidden on this device has not vanished.
  // The replacement goes through `pickDefaultProfile` and not through
  // `profiles[0]`: the first entry may be an unlisted door, and a session
  // whose profile was renamed away must land where a fresh one would, not
  // wherever the config happens to begin.
  useEffect(() => {
    if (chatProfile && profiles?.length) {
      const profileExists = profiles.some(
        (profile) => profile.name === chatProfile
      );
      if (!profileExists) {
        setChatProfile(pickDefaultProfile(profiles, device));
      }
    }
  }, [chatProfile, profiles, device, setChatProfile]);

  // Nothing to choose between: one door is not a selector. Counted over what
  // is *offered*, never over what is on screen — a user who walked through a
  // starter into an unlisted profile would otherwise be shown a two-item
  // selector conjured out of the one entry point plus where they now are.
  // Placed below every hook: the effects above still have to settle the
  // profile.
  if (offeredProfiles.length <= 1) {
    return null;
  }

  const handleClose = () => {
    setOpenDialog(false);
    setNewChatProfile(null);
    navigate?.('/');
  };

  const handleConfirm = (profile: string) => {
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
          if (firstInteraction) {
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
          {visibleProfiles.map((profile) => {
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
