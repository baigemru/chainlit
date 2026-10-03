import { cn, hasMessage } from '@/lib/utils';
import { MutableRefObject } from 'react';

import { FileSpec, useChatData, useChatMessages } from '@chainlit/react-client';

import WaterMark from '@/components/WaterMark';

import ComposerHint from './ComposerHint';
import MessageComposer from './MessageComposer';

interface Props {
  fileSpec: FileSpec;
  onFileUpload: (payload: File[]) => void;
  onFileUploadError: (error: string) => void;
  autoScrollRef: MutableRefObject<boolean>;
  showIfEmptyThread?: boolean;
}

export default function ChatFooter({ showIfEmptyThread, ...props }: Props) {
  const { messages } = useChatMessages();
  const { composer } = useChatData();
  if (!hasMessage(messages) && !showIfEmptyThread) return null;

  return (
    <div className={cn('relative flex flex-col items-center gap-2 w-full')}>
      <MessageComposer {...props} />
      {/* The server's line only: the profile's hint explains an empty
          composer, and this one is past that. Above the watermark, which is
          the deployment's line and not the conversation's. */}
      {composer?.hint ? <ComposerHint>{composer.hint}</ComposerHint> : null}
      <WaterMark />
    </div>
  );
}
