import React, { useState } from 'react';

import { useChatInteract, useConfig } from '@chainlit/react-client';

import { Translator } from '@/components/i18n';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { getDeviceKey, pickDefaultProfile } from '@/hooks/use-mobile';
import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { EditSquare } from '../icons/EditSquare';

type NewChatDialogProps = {
  open: boolean;
  handleClose: () => void;
  handleConfirm: () => void;
};

export const NewChatDialog = ({
  open,
  handleClose,
  handleConfirm
}: NewChatDialogProps) => {
  const handleKeyDown = (event: React.KeyboardEvent) => {
    event.preventDefault();
    if (event.key === 'Enter') {
      handleConfirm();
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        id="new-chat-dialog"
        className="sm:max-w-md"
        onKeyDown={handleKeyDown}
      >
        <DialogHeader>
          <DialogTitle>
            <Translator path="navigation.newChat.dialog.title" />
          </DialogTitle>
          <DialogDescription>
            <Translator path="navigation.newChat.dialog.description" />
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={handleClose}>
            <Translator path="common.actions.cancel" />
          </Button>
          <Button variant="default" onClick={handleConfirm} id="confirm">
            <Translator path="common.actions.confirm" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

interface Props extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  navigate?: (to: string) => void;
  onConfirm?: () => void;
}

const NewChatButton = ({ navigate, onConfirm, ...buttonProps }: Props) => {
  const [open, setOpen] = useState(false);
  const { clear } = useChatInteract();
  const { config } = useConfig();
  const resetKeptTranscript = useResetKeptTranscript();

  const handleClickOpen = () => {
    if (config?.ui?.confirm_new_chat === false) {
      handleConfirm();
    } else {
      setOpen(true);
    }
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleConfirm = () => {
    if (onConfirm) {
      onConfirm();
    } else {
      // A new chat blanks the screen; transcripts kept by returns to a
      // parent thread would otherwise linger above it.
      resetKeptTranscript();
      // A new chat opens where the config says it should, not wherever the
      // last one ended up. The device is read here rather than subscribed to:
      // this is a click, and the answer only matters at this instant.
      const fallback = pickDefaultProfile(
        config?.chatProfiles ?? [],
        getDeviceKey()
      );
      // One descriptor write, so the connect effect never sees a session that
      // is half new chat and half old profile.
      clear(fallback ? { chatProfile: fallback } : {});
      navigate?.('/');
    }
    handleClose();
  };

  return (
    <div>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              id="new-chat-button"
              className="text-muted-foreground hover:text-muted-foreground"
              onClick={handleClickOpen}
              {...buttonProps}
            >
              <EditSquare className="!size-6" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <Translator path="navigation.newChat.dialog.tooltip" />
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <NewChatDialog
        open={open}
        handleClose={handleClose}
        handleConfirm={handleConfirm}
      />
    </div>
  );
};

export default NewChatButton;
