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
  /**
   * Which of the two arrangements to draw, overriding the viewport. Unset is
   * the rule the chat has always followed — the phone gets the pill, the
   * desktop the card — and the welcome screen is the one caller that names
   * one: an empty screen has the whole page to spend, so the card's toolbar
   * row buys nothing there and the pill reads as an invitation to type.
   */
  layout?: 'full' | 'pill';
}

export default function MessageComposer({
  fileSpec,
  onFileUpload,
  onFileUploadError,
  autoScrollRef,
  layout
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
  // Everything below asks this and not `isMobile`: the arrangement is what
  // the classes, the row and the draft's survival all turn on, and the
  // viewport is only its default answer.
  const pill = layout ? layout === 'pill' : isMobile;

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
  // renders, and the composition state with it), so every remount brings it
  // back empty while `value` — now the Recoil draft — still holds the text:
  // an enabled send button over an empty-looking box, sending something
  // nobody can see. Two things remount it. The layouts hang it off different
  // parents, and `useIsMobile` answers false for one render on a phone, so
  // the first load on a phone switches and a drag across 768px does it
  // again; and the route moving from `/` to `/thread/<id>` swaps the whole
  // page element. The mount run of this effect covers the second — which is
  // why it must stay keyed on the layout alone and not be guarded on a
  // change: `value` in the deps would re-inject on every keystroke.
  //
  // Keyed on `pill` rather than on `isMobile`, because `pill` is what decides
  // which parent the textarea hangs off: a caller that pins the arrangement
  // makes a width change no remount at all, and must not be re-injected for
  // one.
  useEffect(() => {
    if (value) inputRef.current?.setValueExtern(value);
  }, [pill]);

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

  // The same three controls, arranged twice. In the pill they share one row
  // with the textarea — Telegram's — because the card spends ~136px of
  // permanent height on a toolbar row of its own, which a phone does not
  // have to spend and an empty welcome screen has no reason to.
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
      // 40px in the pill: the card's 32px is below every tap-target floor,
      // and the pill is the arrangement a thumb meets.
      className={pill ? 'h-10 w-10' : undefined}
    />
  );

  return (
    <div
      id="message-composer"
      className={
        pill
          ? 'bg-accent dark:bg-card rounded-3xl p-1.5 pl-2 w-full flex flex-col'
          : 'bg-accent dark:bg-card rounded-3xl p-3 px-4 w-full min-h-24 flex flex-col'
      }
    >
      {attachments.length > 0 ? (
        <div className="mb-1">
          <Attachments />
        </div>
      ) : null}
      {pill ? (
        // `items-end`, not `items-center`: once the textarea grows past one
        // line the buttons must stay on the pill's bottom edge, next to the
        // line being typed.
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
      ) : (
        <>
          {textarea}
          <div className="flex items-center justify-between">
            <div className="flex items-center -ml-1.5">
              {uploadButton}
              <OpenParentThreadButton />
              <ComposerChevron />
            </div>
            <div className="flex items-center gap-1">{submitButton}</div>
          </div>
        </>
      )}
    </div>
  );
}
