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

  // The face above the heading, or nothing. An app whose description already
  // says who is talking does not need a portrait repeating it, and the tier
  // that goes is only the picture — the profile's description stays, and with
  // it the heading the screen opens with.
  const showAvatar = config?.ui?.welcome_avatar !== false;

  // The picture and the words are two independent questions, and the screen
  // used to answer them as one: everything lived inside `if (icon)`, so a
  // profile that wrote a description and named no icon had it dropped on the
  // floor. That was already wrong and became visible the day the avatar could
  // be switched off — `icon` would have been a field an app was forced to set
  // and forbidden to show.
  const logo = useMemo(() => {
    const currentChatProfile =
      chatProfile && chatProfiles
        ? chatProfiles.find((cp) => cp.name === chatProfile)
        : undefined;
    const icon = currentChatProfile?.icon;

    // The profile's own face where it named one, the application's mark where
    // it did not — and neither once the app has said it wants no picture.
    const picture = !showAvatar ? null : icon ? (
      <img
        className="h-16 w-16 rounded-full"
        src={icon.startsWith('/public') ? apiClient.buildEndpoint(icon) : icon}
      />
    ) : (
      // No `mb-2` of its own any more: the wrapper below carries it, and the
      // two together would double the gap under a bare logo.
      <Logo className="w-[200px]" />
    );

    const description = currentChatProfile?.markdown_description ? (
      // A heading and, under it, the profile's own invitation. The heading
      // keeps the size markdown gives it and loses only the top margin it
      // would carry inside an article; every paragraph after it is the
      // subtitle's register — small, muted, centred — because a profile that
      // writes a second line means it to read as a subtitle and not as the
      // first paragraph of a document.
      <Markdown
        allowHtml={allowHtml}
        latex={latex}
        renderMarkdown={true}
        className="text-center [&_h1]:mt-0 [&_h2]:mt-0 [&_h2]:border-b-0 [&_h3]:mt-0 [&_h4]:mt-0 [&_div]:mt-1 [&_div]:text-sm [&_div]:leading-snug [&_div]:text-muted-foreground"
      >
        {currentChatProfile.markdown_description}
      </Markdown>
    ) : null;

    // Nothing at all, not an empty box: the screen's first tier is the
    // composer once there is neither a picture nor a word to put above it,
    // and the margin the wrapper would still draw around nothing is the whole
    // point of the switch.
    if (!picture && !description) return null;

    return (
      <div className="flex flex-col gap-2 mb-2 items-center">
        {picture}
        {description}
      </div>
    );
    // `showAvatar` is in the deps and not only in the body: the config
    // arrives over the wire, so it changes after the first render.
  }, [chatProfiles, chatProfile, showAvatar]);

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
    // Centred by auto margins and not by `justify-center`, and with no
    // negative margin standing in for the header: a flex parent that centres
    // an item taller than itself overflows it in both directions, and the
    // half above the scroll container's top edge cannot be scrolled back to —
    // which is how the profile's avatar came to be cut off and the last
    // section to sit under the bottom edge. Auto margins collapse to nothing
    // once the content is the taller of the two, so the whole screen stays
    // reachable.
    <div
      id="welcome-screen"
      className={cn(
        'flex flex-col gap-4 w-full my-auto items-center welcome-screen mx-auto transition-opacity duration-500 opacity-0 delay-100',
        isVisible && 'opacity-100'
      )}
    >
      {logo}
      {/* One line, pinned: the entry screen is an invitation to type, and
          the card's toolbar row under an empty textarea reads as a form to
          fill in. The chat below keeps whichever arrangement its width
          earns. */}
      <MessageComposer {...props} layout="pill" />
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
