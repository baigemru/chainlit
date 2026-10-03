import { FileSpec, useConfig } from '@chainlit/react-client';

import { Translator } from '@/components/i18n';
import { PaperClip } from '@/components/icons/PaperClip';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger
} from '@/components/ui/tooltip';

import { useUpload } from '@/hooks/useUpload';

interface UploadButtonProps {
  disabled?: boolean;
  fileSpec: FileSpec;
  onFileUpload: (files: File[]) => void;
  onFileUploadError: (error: string) => void;
}

export const UploadButton = ({
  disabled = false,
  fileSpec,
  onFileUpload,
  onFileUploadError
}: UploadButtonProps) => {
  const { config } = useConfig();
  const upload = useUpload({
    spec: fileSpec,
    onResolved: (payloads: File[]) => onFileUpload(payloads),
    onError: onFileUploadError,
    options: { noDrag: true }
  });

  if (!upload) return null;
  const { getRootProps, getInputProps } = upload;

  if (!config?.features.spontaneous_file_upload?.enabled) return null;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          {/* `flex`, not `inline-block`: on a text baseline the span grew a
              descender's gap under the button and lifted it off the centre
              of the composer's left slot. */}
          <span className="flex">
            <input
              id="upload-button-input"
              className="hidden"
              {...getInputProps()}
            />
            <Button
              id={disabled ? 'upload-button-loading' : 'upload-button'}
              variant="ghost"
              size="icon"
              className="hover:bg-muted"
              disabled={disabled}
              {...getRootProps()}
            >
              <PaperClip className="!size-6" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          <p>
            <Translator path="chat.input.actions.attachFiles" />
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default UploadButton;
