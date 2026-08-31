import {
  MutableRefObject,
  useCallback,
  useEffect,
  useRef,
  useState
} from 'react';
import { useRecoilState } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

import {
  FileSpec,
  IStep,
  useAuth,
  useChatData,
  useChatInteract
} from '@chainlit/react-client';

import { useTranslation } from 'components/i18n/Translator';

import { useQuery } from '@/hooks/query';
import { useIsMobile } from '@/hooks/use-mobile';

import { IAttachment, attachmentsState } from 'state/chat';

import { Attachments } from './Attachments';
import Input, { InputMethods } from './Input';
import OpenParentThreadButton from './OpenParentThreadButton';
import SubmitButton from './SubmitButton';
import UploadButton from './UploadButton';

interface Props {
  fileSpec: FileSpec;
  onFileUpload: (payload: File[]) => void;
  onFileUploadError: (error: string) => void;
  autoScrollRef: MutableRefObject<boolean>;
}

export default function MessageComposer({
  fileSpec,
  onFileUpload,
  onFileUploadError,
  autoScrollRef
}: Props) {
  const inputRef = useRef<InputMethods>(null);
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useRecoilState(attachmentsState);
  const { t } = useTranslation();

  const { user } = useAuth();
  const { sendMessage, replyMessage } = useChatInteract();
  const { askUser, disabled: _disabled } = useChatData();

  const disabled = _disabled || !!attachments.find((a) => !a.uploaded);

  const isMobile = useIsMobile();

  let promptValue = '';
  try {
    const query = useQuery();
    promptValue = query.get('prompt') || '';
  } catch {
    console.warn('Could not parse query parameters');
  }

  const [promptUsed, setPromptUsed] = useState(false);

  const onPaste = useCallback(
    (event: ClipboardEvent) => {
      if (event.clipboardData && event.clipboardData.items) {
        const items = Array.from(event.clipboardData.items);

        // If no text data, check for files (e.g., images)
        items.forEach((item) => {
          if (item.kind === 'file') {
            const file = item.getAsFile();
            if (file) {
              onFileUpload([file]);
            }
          }
        });
      }
    },
    [onFileUpload]
  );

  const onSubmit = useCallback(
    async (msg: string, attachments?: IAttachment[]) => {
      const message: IStep = {
        threadId: '',
        id: uuidv4(),
        name: user?.identifier || 'User',
        type: 'user_message',
        output: msg,
        createdAt: new Date().toISOString(),
        metadata: { location: window.location.href }
      };

      const fileReferences = attachments
        ?.filter((a) => !!a.serverId)
        .map((a) => ({ id: a.serverId! }));

      if (autoScrollRef) {
        autoScrollRef.current = true;
      }
      sendMessage(message, fileReferences);
    },
    [user, sendMessage, autoScrollRef]
  );

  const onReply = useCallback(
    async (msg: string) => {
      const message: IStep = {
        threadId: '',
        id: uuidv4(),
        name: user?.identifier || 'User',
        type: 'user_message',
        output: msg,
        createdAt: new Date().toISOString(),
        metadata: { location: window.location.href }
      };

      replyMessage(message);
      if (autoScrollRef) {
        autoScrollRef.current = true;
      }
    },
    [user, replyMessage, autoScrollRef]
  );

  const submit = useCallback(() => {
    if (disabled || (value.trim() === '' && attachments.length === 0)) {
      return;
    }

    if (askUser) {
      onReply(value);
    } else {
      onSubmit(value, attachments);
    }

    setAttachments([]);
    setValue(''); // Clear the value state
    inputRef.current?.reset();
  }, [
    value,
    disabled,
    askUser,
    attachments,
    setAttachments,
    onSubmit,
    onReply
  ]);

  // The two layouts hang the textarea off different parents, so React
  // remounts it on every switch and it comes back empty — while `value`, and
  // with it the enabled send button, still holds the draft. `useIsMobile`
  // answers false for one render on a phone, so the very first load switches;
  // dragging a window across 768px mid-sentence does it again. Deliberately
  // keyed on the layout alone: `value` in the deps would re-inject on every
  // keystroke.
  useEffect(() => {
    if (value) inputRef.current?.setValueExtern(value);
  }, [isMobile]);

  useEffect(() => {
    if (inputRef.current && promptValue && !promptUsed) {
      const prompt = promptValue;
      if (prompt) {
        if (prompt.length > 1000) {
          inputRef.current?.setValueExtern(prompt.slice(0, 1000));
        } else {
          inputRef.current?.setValueExtern(prompt);
        }
        setPromptUsed(true);
      }
    }
  }, [promptValue, promptUsed]);

  // The same three controls, arranged twice. On a phone they share one row
  // with the textarea — Telegram's pill — because the desktop card spends
  // ~136px of permanent height on a toolbar row of its own, and a phone has
  // no such height to spend.
  const uploadButton = (
    <UploadButton
      disabled={disabled}
      fileSpec={fileSpec}
      onFileUploadError={onFileUploadError}
      onFileUpload={onFileUpload}
    />
  );
  const textarea = (
    <Input
      ref={inputRef}
      id="chat-input"
      autoFocus={!isMobile}
      onChange={setValue}
      onPaste={onPaste}
      onEnter={submit}
      placeholder={t('chat.input.placeholder')}
    />
  );
  const submitButton = (
    <SubmitButton
      onSubmit={submit}
      disabled={disabled || (!value.trim() && attachments.length === 0)}
      // 40px on a phone: the desktop 32px is below every tap-target floor.
      className={isMobile ? 'h-10 w-10' : undefined}
    />
  );

  return (
    <div
      id="message-composer"
      className={
        isMobile
          ? 'bg-accent dark:bg-card rounded-3xl p-1.5 pl-2 w-full flex flex-col'
          : 'bg-accent dark:bg-card rounded-3xl p-3 px-4 w-full min-h-24 flex flex-col'
      }
    >
      {attachments.length > 0 ? (
        <div className="mb-1">
          <Attachments />
        </div>
      ) : null}
      {isMobile ? (
        // `items-end`, not `items-center`: once the textarea grows past one
        // line the buttons must stay on the pill's bottom edge, next to the
        // line being typed.
        <div className="flex items-end gap-1">
          {uploadButton}
          <OpenParentThreadButton />
          {textarea}
          {submitButton}
        </div>
      ) : (
        <>
          {textarea}
          <div className="flex items-center justify-between">
            <div className="flex items-center -ml-1.5">
              {uploadButton}
              <OpenParentThreadButton />
            </div>
            <div className="flex items-center gap-1">{submitButton}</div>
          </div>
        </>
      )}
    </div>
  );
}
