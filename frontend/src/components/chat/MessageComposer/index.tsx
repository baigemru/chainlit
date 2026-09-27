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

import { IAttachment, attachmentsState, composerDraftState } from 'state/chat';

import { Attachments } from './Attachments';
import ComposerChevron from './ComposerChevron';
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

/**
 * The composer: one pill, the textarea and its three controls on one line,
 * on every width.
 *
 * There used to be a second arrangement, a card with a toolbar row under the
 * textarea, which the desktop got by default. Both screens that draw a
 * composer -- the welcome screen and the chat's footer -- came to ask for the
 * pill instead: the row spent ~136px of permanent height on three buttons
 * the pill carries beside the text, and on an empty page it read as a form
 * to fill in. With no caller left for it, the card went too.
 */
export default function MessageComposer({
  fileSpec,
  onFileUpload,
  onFileUploadError,
  autoScrollRef
}: Props) {
  const inputRef = useRef<InputMethods>(null);
  // Above this component, not inside it: the composer is remounted by the
  // route moving under it (see `composerDraftState`), and a draft in
  // `useState` would go with it.
  const [value, setValue] = useRecoilState(composerDraftState);
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

  // The textarea keeps its own copy of the draft (`Input` owns the value it
  // renders, and the composition state with it), so a remount brings it back
  // empty while `value` — the Recoil draft — still holds the text: an enabled
  // send button over an empty-looking box, sending something nobody can see.
  // The route moving from `/` to `/thread/<id>` swaps the whole page element
  // and remounts it. On mount only: `value` in the deps would re-inject on
  // every keystroke.
  useEffect(() => {
    if (value) inputRef.current?.setValueExtern(value);
  }, []);

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
      canSend={!disabled && (!!value.trim() || attachments.length > 0)}
      // 40px: the button's own 32px is below every tap-target floor, and a
      // thumb meets this pill on a phone.
      className="h-10 w-10"
    />
  );

  return (
    <div
      id="message-composer"
      className="bg-accent dark:bg-card rounded-3xl p-1.5 pl-2 w-full flex flex-col"
    >
      {attachments.length > 0 ? (
        <div className="mb-1">
          <Attachments />
        </div>
      ) : null}
      {/* `items-end`, not `items-center`: once the textarea grows past one
          line the buttons must stay on the pill's bottom edge, next to the
          line being typed. */}
      <div className="flex items-end gap-1">
        {uploadButton}
        <OpenParentThreadButton />
        {/* Last in the left slot, after whichever of the two above render:
            the panel is the one control here that is always available, and
            it must not shift the buttons whose position people learn. */}
        <ComposerChevron />
        {textarea}
        {submitButton}
      </div>
    </div>
  );
}
