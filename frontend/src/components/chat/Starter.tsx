import { cn } from '@/lib/utils';
import { useCallback, useContext } from 'react';
import { useSetRecoilState } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

import {
  ChainlitContext,
  IStarter,
  IStep,
  useAuth,
  useChatData,
  useChatInteract,
  useChatSession
} from '@chainlit/react-client';

import { Button } from '@/components/ui/button';

import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { IAttachment, attachmentsState } from '@/state/chat';

interface StarterProps {
  starter: IStarter;
}

export default function Starter({ starter }: StarterProps) {
  const apiClient = useContext(ChainlitContext);
  const { sendMessage, clear } = useChatInteract();
  const { setChatProfile } = useChatSession();
  const { loading, connected } = useChatData();
  const { user } = useAuth();
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const resetKeptTranscript = useResetKeptTranscript();

  const disabled = loading || !connected;

  const onSubmit = useCallback(async () => {
    // A starter naming a profile is a door, not a question: it moves the user
    // to that profile and says nothing on their behalf — no server round trip
    // and no line in the transcript. The same teardown as a manual selection
    // (ChatProfiles.handleConfirm), minus its dialog: starters are only on
    // screen while the chat is still empty, so there is nothing to confirm.
    if (starter.profile) {
      setChatProfile(starter.profile);
      setAttachments([]);
      resetKeptTranscript();
      clear();
      return;
    }

    const message: IStep = {
      threadId: '',
      id: uuidv4(),
      command: starter.command,
      name: user?.identifier || 'User',
      type: 'user_message',
      output: starter.message,
      createdAt: new Date().toISOString(),
      metadata: { location: window.location.href }
    };

    sendMessage(message, []);
  }, [
    user,
    sendMessage,
    starter,
    setChatProfile,
    setAttachments,
    resetKeptTranscript,
    clear
  ]);

  const highlight = starter.highlight === true;

  return (
    <Button
      id={`starter-${starter.label.trim().toLowerCase().replaceAll(' ', '-')}`}
      variant={highlight ? 'default' : 'outline'}
      className={cn(
        highlight
          ? 'w-full justify-center h-12 text-base rounded-2xl'
          : 'w-fit justify-start rounded-3xl'
      )}
      disabled={disabled}
      onClick={onSubmit}
    >
      <div className="flex gap-2">
        {starter.icon ? (
          <img
            className="h-5 w-5 rounded-md"
            src={
              starter.icon?.startsWith('/public')
                ? apiClient.buildEndpoint(starter.icon)
                : starter.icon
            }
            alt={starter.label}
          />
        ) : null}
        <p
          className={cn(
            'truncate',
            highlight ? 'text-base' : 'text-sm text-muted-foreground'
          )}
        >
          {starter.label}
        </p>
      </div>
    </Button>
  );
}
