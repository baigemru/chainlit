import { cn, hasMessage } from '@/lib/utils';
import {
  MutableRefObject,
  useContext,
  useEffect,
  useMemo,
  useState
} from 'react';

import {
  ChainlitContext,
  FileSpec,
  useChatMessages,
  useChatSession,
  useConfig
} from '@chainlit/react-client';

import { Logo } from '@/components/Logo';
import { Markdown } from '@/components/Markdown';

import MessageComposer from './MessageComposer';
import Starters from './Starters';

interface Props {
  fileSpec: FileSpec;
  onFileUpload: (payload: File[]) => void;
  onFileUploadError: (error: string) => void;
  autoScrollRef: MutableRefObject<boolean>;
}

export default function WelcomeScreen(props: Props) {
  const apiClient = useContext(ChainlitContext);
  const { config } = useConfig();
  const { chatProfile } = useChatSession();
  const { messages } = useChatMessages();
  const [isVisible, setIsVisible] = useState(false);

  const chatProfiles = config?.chatProfiles;

  const allowHtml = config?.features?.unsafe_allow_html;
  const latex = config?.features?.latex;

  useEffect(() => {
    setIsVisible(true);
  }, []);

  const logo = useMemo(() => {
    if (chatProfile && chatProfiles) {
      const currentChatProfile = chatProfiles.find(
        (cp) => cp.name === chatProfile
      );
      if (currentChatProfile?.icon) {
        return (
          <div className="flex flex-col gap-2 mb-2 items-center">
            <img
              className="h-16 w-16 rounded-full"
              src={
                currentChatProfile?.icon.startsWith('/public')
                  ? apiClient.buildEndpoint(currentChatProfile?.icon)
                  : currentChatProfile?.icon
              }
            />
            {currentChatProfile?.markdown_description ? (
              <Markdown
                allowHtml={allowHtml}
                latex={latex}
                renderMarkdown={true}
              >
                {currentChatProfile.markdown_description}
              </Markdown>
            ) : null}
          </div>
        );
      }
    }

    return <Logo className="w-[200px] mb-2" />;
  }, [chatProfiles, chatProfile]);

  // The line under the composer, in the profile's own words: what pressing
  // Enter will do. It is markdown because the applications that want one
  // want a link in it, and it is drawn only on the empty screen — once the
  // conversation has started the composer no longer needs explaining.
  const composerHint = useMemo(
    () => chatProfiles?.find((cp) => cp.name === chatProfile)?.composer_hint,
    [chatProfiles, chatProfile]
  );

  if (hasMessage(messages)) return null;

  return (
    <div
      id="welcome-screen"
      className={cn(
        'flex flex-col -mt-[60px] gap-4 w-full flex-grow items-center justify-center welcome-screen mx-auto transition-opacity duration-500 opacity-0 delay-100',
        isVisible && 'opacity-100'
      )}
    >
      {logo}
      <MessageComposer {...props} />
      {composerHint ? (
        <div className="composer-hint max-w-full -mt-2">
          {/* The same register as the watermark: small, muted, one
              paragraph with no margin of its own. */}
          <Markdown
            allowHtml={allowHtml}
            latex={latex}
            renderMarkdown={true}
            className="text-xs text-muted-foreground text-center [&_p]:m-0 [&_div]:mt-0 [&_div]:leading-snug"
          >
            {composerHint}
          </Markdown>
        </div>
      ) : null}
      <Starters />
    </div>
  );
}
