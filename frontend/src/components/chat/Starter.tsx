import { cn } from '@/lib/utils';
import { cva } from 'class-variance-authority';
import { useCallback, useContext } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSetRecoilState } from 'recoil';
import { v4 as uuidv4 } from 'uuid';

import {
  type ChainlitAPI,
  ChainlitContext,
  IStarter,
  IStep,
  type StarterLayout,
  useAuth,
  useChatData,
  useChatInteract,
  useChatSession
} from '@chainlit/react-client';

import { Button } from '@/components/ui/button';

import { useResetKeptTranscript } from '@/hooks/useParentThread';

import { IAttachment, attachmentsState } from '@/state/chat';

/**
 * One starter, three densities. A second component per density would have
 * meant three copies of the click policy below, and the click policy is the
 * part that must not drift; what differs between a tile, a plate and a row is
 * only which box the same label goes in.
 *
 * The overrides against `buttonVariants` are deliberate: its base carries
 * `whitespace-nowrap` and its default size an `h-10`, both of which clip a
 * plate the moment its description wraps.
 */
export const starterVariants = cva('', {
  variants: {
    layout: {
      // No width class at all: in the flat list's flex row that is the old
      // `w-fit`, and in a section's grid it lets the tile stretch to its
      // column, which is what puts two of them side by side on a phone.
      tiles:
        'flex flex-col items-center justify-center gap-0.5 h-auto min-h-[3.5rem] px-4 py-3 rounded-2xl text-center whitespace-normal',
      plates:
        'flex flex-col items-start gap-1 w-full h-auto min-h-[6rem] p-4 rounded-2xl text-left whitespace-normal border border-input bg-background hover:bg-accent hover:text-accent-foreground',
      rows: 'flex w-full items-center gap-2 h-auto justify-start px-3 py-2 rounded-lg text-left whitespace-normal font-normal border border-input bg-background'
    },
    highlight: {
      true: '',
      false: ''
    }
  },
  compoundVariants: [
    // A highlighted tile is the one the section wants pressed, not a banner:
    // it says so with the accent on its border and its label. Filling it —
    // which it used to do, at full width and one to a line — made two equal
    // offers into one advertisement and threw the section's grid away.
    {
      layout: 'tiles',
      highlight: true,
      class: 'border-primary/60 text-primary hover:text-primary'
    },
    {
      layout: 'plates',
      highlight: true,
      class:
        'border-transparent bg-primary text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground'
    },
    {
      layout: 'rows',
      highlight: true,
      class: 'bg-accent text-accent-foreground'
    }
  ],
  defaultVariants: {
    layout: 'tiles',
    highlight: false
  }
});

/**
 * An icon the application serves out of its own `public/` reaches the browser
 * only through the API client: the deployment may sit under a `--root-path`,
 * and a bare `/public/x.png` then resolves against the wrong origin prefix.
 * Anything else — an absolute URL, a data URI — is already an address.
 *
 * Exported because the section heading draws an icon the same way, and two
 * copies of this rule are how one of them silently stops working.
 */
export const starterIconSrc = (
  icon: string,
  apiClient: Pick<ChainlitAPI, 'buildEndpoint'>
): string =>
  icon.startsWith('/public') ? apiClient.buildEndpoint(icon) : icon;

interface StarterProps {
  starter: IStarter;
  layout?: StarterLayout;
}

export default function Starter({ starter, layout = 'tiles' }: StarterProps) {
  const navigate = useNavigate();
  const apiClient = useContext(ChainlitContext);
  const { sendMessage, clear } = useChatInteract();
  const { setChatProfile } = useChatSession();
  const { loading, connected } = useChatData();
  const { user } = useAuth();
  const setAttachments = useSetRecoilState<IAttachment[]>(attachmentsState);
  const resetKeptTranscript = useResetKeptTranscript();

  const onSubmit = useCallback(async () => {
    // A starter naming an address is a link, and only a link: it must not
    // tear the session down, because the page it opens is a dialog over the
    // chat that goes on living behind it.
    if (starter.href) {
      navigate(starter.href);
      return;
    }

    // A starter naming a profile is a door, not a question: it moves the user
    // to that profile and says nothing on their behalf — no server round trip
    // and no line in the transcript. The same teardown as a manual selection
    // (ChatProfiles.handleConfirm), minus its dialog: starters are only on
    // screen while the chat is still empty, so there is nothing to confirm.
    if (starter.profile) {
      setChatProfile(starter.profile);
      setAttachments([]);
      resetKeptTranscript();
      clear();
      // Home in the same breath as the clear, like NewChat. Starters are on
      // screen while the chat is empty, and an empty chat still has an
      // address: staying on /thread/<the chat we just gave up> would have
      // ThreadAddressSync read the route as a request and resume that thread
      // -- on the old profile, undoing the door the user just walked through.
      navigate('/');
      return;
    }

    const message: IStep = {
      threadId: '',
      id: uuidv4(),
      command: starter.command,
      name: user?.identifier || 'User',
      type: 'user_message',
      output: starter.message,
      createdAt: new Date().toISOString(),
      metadata: { location: window.location.href }
    };

    sendMessage(message, []);
  }, [
    user,
    navigate,
    sendMessage,
    starter,
    setChatProfile,
    setAttachments,
    resetKeptTranscript,
    clear
  ]);

  const highlight = starter.highlight === true;
  // Two different facts wearing one attribute. `loading || !connected` is the
  // engine saying "not now"; `starter.disabled` is the application saying
  // "not this one" — which is why it is also announced with `aria-disabled`,
  // the only one of the two a screen reader user is meant to hear about.
  const inert = starter.disabled === true;
  const muted =
    highlight && layout === 'plates'
      ? 'text-primary-foreground/80'
      : 'text-muted-foreground';

  const icon = starter.icon ? (
    <img
      className="h-5 w-5 shrink-0 rounded-md"
      src={starterIconSrc(starter.icon, apiClient)}
      alt={starter.label}
    />
  ) : null;

  const body =
    layout === 'plates' ? (
      <>
        <div className="flex w-full items-center gap-2">
          {icon}
          <span className="font-medium text-sm">{starter.label}</span>
        </div>
        {starter.description ? (
          <span className={cn('text-xs', muted)}>{starter.description}</span>
        ) : null}
        {starter.caption ? (
          <span className={cn('mt-auto self-end font-mono text-xs', muted)}>
            {starter.caption}
          </span>
        ) : null}
      </>
    ) : layout === 'rows' ? (
      // Three columns on one line: what the errand is, what it takes, what it
      // costs. The name never shrinks and the price never wraps, so the
      // description is the only part that gives ground — which is the right
      // one to lose, and the reason it is not the one left stranded at the
      // far edge of the row.
      <>
        {icon}
        <span className="shrink-0 text-sm font-medium">{starter.label}</span>
        {starter.description ? (
          <span className={cn('min-w-0 flex-1 text-xs', muted)}>
            {starter.description}
          </span>
        ) : null}
        {starter.caption ? (
          <span className={cn('ml-auto shrink-0 font-mono text-xs', muted)}>
            {starter.caption}
          </span>
        ) : null}
      </>
    ) : (
      <>
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-sm font-medium">{starter.label}</span>
        </div>
        {starter.caption ? (
          <span className={cn('font-mono text-xs', muted)}>
            {starter.caption}
          </span>
        ) : null}
      </>
    );

  return (
    <Button
      id={`starter-${starter.label.trim().toLowerCase().replaceAll(' ', '-')}`}
      // Outline whatever the highlight says: a filled tile among outlined
      // ones is a different kind of thing, and the two offers in a section
      // are the same kind of thing.
      variant={layout === 'tiles' ? 'outline' : 'ghost'}
      className={cn(starterVariants({ layout, highlight }))}
      disabled={inert || loading || !connected}
      aria-disabled={inert || undefined}
      onClick={onSubmit}
    >
      {body}
    </Button>
  );
}
