import { cn } from '@/lib/utils';
import { MessageContext } from 'contexts/MessageContext';
import { Upload } from 'lucide-react';
import { useContext, useState } from 'react';

import { IAsk, IFileRef } from '@chainlit/react-client';

import { Translator } from '@/components/i18n';
import { useTranslation } from '@/components/i18n/Translator';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import { useIsMobile } from '@/hooks/use-mobile';
import { useUpload } from 'hooks/useUpload';

interface UploadState {
  progress: number;
  uploaded: boolean;
  cancel: () => void;
  fileRef?: IFileRef;
}

interface _AskFileButtonProps {
  askUser: IAsk;
  parentId?: string;
  uploadFile: (
    file: File,
    onProgress: (progress: number) => void,
    parentId?: string
  ) => {
    xhr: XMLHttpRequest;
    promise: Promise<IFileRef>;
  };
  onError: (error: string) => void;
}

const CircularProgress = ({ value }: { value: number }) => {
  const size = 24;
  const strokeWidth = 2;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (value / 100) * circumference;

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg
        className="absolute"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
      >
        <circle
          className="text-muted-foreground/20"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          stroke="currentColor"
        />
        <circle
          className="text-primary transition-all duration-300 ease-in-out"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          stroke="currentColor"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
    </div>
  );
};

const _AskFileButton = ({
  askUser,
  uploadFile,
  onError
}: _AskFileButtonProps) => {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  const [uploads, setUploads] = useState<UploadState[]>([]);

  const uploading = uploads.some((upload) => !upload.uploaded);
  const progress = uploads.reduce(
    (acc, upload) => acc + upload.progress / uploads.length,
    0
  );

  const onResolved = (files: File[]) => {
    if (uploading || askUser.awaitingReply) return;

    const promises: Promise<IFileRef>[] = [];

    const newUploads = files.map((file, index) => {
      const { xhr, promise } = uploadFile(
        file,
        (progress) => {
          setUploads((prev) =>
            prev.map((upload, i) => {
              if (i === index) {
                return { ...upload, progress };
              }
              return upload;
            })
          );
        },
        askUser?.parentId
      );
      promises.push(promise);
      return { progress: 0, uploaded: false, cancel: () => xhr.abort() };
    });

    Promise.all(promises)
      .then((fileRefs) => {
        // Mark the batch finished so the dropzone unlocks: after a
        // reconnect the same form may need a second attempt.
        setUploads((prev) => prev.map((u) => ({ ...u, uploaded: true })));
        askUser.callback(fileRefs);
      })
      .catch((error) => {
        onError(
          `${t('chat.fileUpload.errors.failed')}: ${
            typeof error === 'object' && error !== null
              ? (error.message ?? error)
              : error
          }`
        );
        setUploads((prev) => {
          prev.forEach((u) => u.cancel());
          return [];
        });
      });

    setUploads(newUploads);
  };

  const upload = useUpload({
    spec: askUser.spec,
    onResolved: onResolved,
    onError: (error: string) => onError(error)
  });

  if (!upload) return null;
  const { getRootProps, getInputProps } = upload;

  const hint = (
    <div className="flex flex-col min-w-0">
      <p className="text-sm font-medium truncate">
        <Translator path="chat.fileUpload.dragDrop" />
      </p>
      <p className="text-sm text-muted-foreground truncate">
        <Translator path="chat.fileUpload.sizeLimit" /> {askUser.spec.maxSizeMb}
        mb
      </p>
    </div>
  );

  return (
    <Card className="w-full mt-2">
      <div
        {...getRootProps({ className: 'dropzone' })}
        // On a narrow screen the row cannot hold the wording and the button
        // side by side: the button ends up past the edge of the card.
        className={cn(
          'p-4',
          isMobile ? 'flex flex-col items-stretch gap-2' : 'flex items-center'
        )}
      >
        <input id="ask-button-input" {...getInputProps()} />
        {/* The button leads on a phone: it is what the card is for, and the
            wording under it is only a reminder of the size limit. */}
        {isMobile ? null : hint}
        <Button
          id={uploading ? 'ask-upload-button-loading' : 'ask-upload-button'}
          disabled={uploading || askUser.awaitingReply}
          className={cn(isMobile ? 'w-full min-w-0' : 'ml-auto')}
          variant={uploading ? 'ghost' : 'default'}
        >
          {uploading ? (
            <CircularProgress value={progress} />
          ) : (
            <>
              <Upload className="w-4 h-4 mr-2" />
              <Translator path="chat.fileUpload.browse" />
            </>
          )}
        </Button>
        {isMobile ? hint : null}
      </div>
    </Card>
  );
};

interface AskFileButtonProps {
  messageId: string;
  onError: (error: string) => void;
}

const AskFileButton = ({ messageId, onError }: AskFileButtonProps) => {
  const messageContext = useContext(MessageContext);
  const belongsToMessage = messageContext.askUser?.spec.stepId === messageId;
  const isAskFile = messageContext.askUser?.spec.type === 'file';

  if (!belongsToMessage || !isAskFile || !messageContext?.uploadFile)
    return null;

  return (
    <_AskFileButton
      onError={onError}
      uploadFile={messageContext.uploadFile}
      askUser={messageContext.askUser!}
    />
  );
};

export { AskFileButton };
