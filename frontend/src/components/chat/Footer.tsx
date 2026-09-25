import { cn, hasMessage } from '@/lib/utils';
import { MutableRefObject } from 'react';

import { FileSpec, useChatMessages } from '@chainlit/react-client';

import WaterMark from '@/components/WaterMark';

import MessageComposer from './MessageComposer';

interface Props {
  fileSpec: FileSpec;
  onFileUpload: (payload: File[]) => void;
  onFileUploadError: (error: string) => void;
  autoScrollRef: MutableRefObject<boolean>;
  showIfEmptyThread?: boolean;
}

/**
 * The composer under a conversation, always the pill.
 *
 * The card's toolbar row spent ~136px of permanent height at the foot of
 * every chat, for three buttons the pill already holds on its one line
 * beside the text -- and the conversation is what that height is for. Named
 * here rather than left to the viewport, so the chat and the welcome screen
 * are the same box on every width and moving between them is no remount.
 */
export default function ChatFooter({ showIfEmptyThread, ...props }: Props) {
  const { messages } = useChatMessages();
  if (!hasMessage(messages) && !showIfEmptyThread) return null;

  return (
    <div className={cn('relative flex flex-col items-center gap-2 w-full')}>
      <MessageComposer {...props} layout="pill" />
      <WaterMark />
    </div>
  );
}
