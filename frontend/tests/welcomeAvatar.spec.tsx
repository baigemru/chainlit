import { render, screen } from '@testing-library/react';
import { createContext } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChatProfile } from '@chainlit/react-client';

import WelcomeScreen from '@/components/chat/WelcomeScreen';

/**
 * What the entry screen opens with, and how it asks for the first line.
 *
 * Two claims, one screen. `ui.welcome_avatar` decides whether there is a
 * picture above the heading at all — the profile's icon where it has one, the
 * app logo where it has not — and the profile's description is not part of
 * that decision: an app that drops the portrait keeps the words. And the
 * composer here is always the pill, whatever the viewport says, because the
 * empty screen is an invitation to type and not a form to fill in.
 */

const mockUseConfig = vi.fn();
const mockUseChatSession = vi.fn();
const mockComposerLayout = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  MOBILE_BREAKPOINT: 768,
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path
  }),
  useChatMessages: () => ({ messages: [] }),
  useChatSession: () => mockUseChatSession(),
  useConfig: () => mockUseConfig()
}));

// The composer's own arrangement is `composerCompact.spec.tsx`'s subject;
// what this file claims is which one the screen asks for.
vi.mock('@/components/chat/MessageComposer', () => ({
  default: ({ layout }: { layout?: string }) => {
    mockComposerLayout(layout);
    return <div>composer</div>;
  }
}));
vi.mock('@/components/chat/Starters', () => ({
  default: () => <div>starters</div>
}));
// A name in the document rather than the real `<img>`: `Logo` reaches for the
// theme and the API client, and whether it rendered at all is the assertion.
vi.mock('@/components/Logo', () => ({ Logo: () => <div>logo</div> }));
vi.mock('@/components/Markdown', () => ({
  Markdown: ({ children }: { children: string }) => <div>{children}</div>
}));

const profile = (name: string, rest: Partial<ChatProfile> = {}): ChatProfile =>
  ({
    name,
    default: false,
    markdown_description: '',
    ...rest
  }) as ChatProfile;

const props = {
  fileSpec: { accept: [] } as any,
  onFileUpload: () => undefined,
  onFileUploadError: () => undefined,
  autoScrollRef: { current: true }
};

const withConfig = (
  ui: Record<string, unknown>,
  profiles: ChatProfile[] = [],
  current?: string
) => {
  mockUseConfig.mockReturnValue({ config: { ui, chatProfiles: profiles } });
  mockUseChatSession.mockReturnValue({ chatProfile: current });
};

const PROFILE = profile('Entry', {
  icon: '/public/panda.svg',
  markdown_description: '### What are we looking for?'
});

const avatar = (container: HTMLElement) =>
  container.querySelector('img[src="/public/panda.svg"]');

beforeEach(() => {
  vi.clearAllMocks();
});

describe('the welcome screen’s avatar', () => {
  it('draws the profile’s icon when the app said nothing', () => {
    withConfig({}, [PROFILE], 'Entry');

    const { container } = render(<WelcomeScreen {...props} />);

    expect(avatar(container)).not.toBeNull();
    expect(
      screen.getByText('### What are we looking for?')
    ).toBeInTheDocument();
  });

  it('drops the profile’s icon and keeps its words', () => {
    // The tier that goes is the picture alone: the heading the screen opens
    // with is in the description, and dropping it would leave a composer
    // floating on an empty page.
    withConfig({ welcome_avatar: false }, [PROFILE], 'Entry');

    const { container } = render(<WelcomeScreen {...props} />);

    expect(avatar(container)).toBeNull();
    expect(
      screen.getByText('### What are we looking for?')
    ).toBeInTheDocument();
  });

  it('draws the app logo when no profile names an icon', () => {
    withConfig({}, [profile('Plain')], 'Plain');

    render(<WelcomeScreen {...props} />);

    expect(screen.getByText('logo')).toBeInTheDocument();
  });

  it('draws no logo either when the app turned the avatar off', () => {
    withConfig({ welcome_avatar: false }, [profile('Plain')], 'Plain');

    const { container } = render(<WelcomeScreen {...props} />);

    expect(screen.queryByText('logo')).not.toBeInTheDocument();
    // Nothing at all, not an empty box: with neither a picture nor a word to
    // put above it the composer is the screen's first tier, and a wrapper
    // standing empty would still spend its `mb-2` on nothing.
    const screenRoot = container.querySelector('#welcome-screen')!;
    expect(screenRoot.firstElementChild!.textContent).toBe('composer');
  });

  it('draws a description from a profile that named no icon', () => {
    // The picture and the words are two questions. They used to be one — the
    // description lived inside `if (icon)` — so a profile that wrote an
    // invitation and named no icon had it dropped, and `icon` was a field an
    // app had to set to be allowed to speak.
    withConfig(
      {},
      [profile('Plain', { markdown_description: '### Ask me anything' })],
      'Plain'
    );

    render(<WelcomeScreen {...props} />);

    expect(screen.getByText('### Ask me anything')).toBeInTheDocument();
    // And the app's own mark is still the picture above it.
    expect(screen.getByText('logo')).toBeInTheDocument();
  });

  it('keeps that description when the avatar is off as well', () => {
    withConfig(
      { welcome_avatar: false },
      [profile('Plain', { markdown_description: '### Ask me anything' })],
      'Plain'
    );

    render(<WelcomeScreen {...props} />);

    expect(screen.getByText('### Ask me anything')).toBeInTheDocument();
    expect(screen.queryByText('logo')).not.toBeInTheDocument();
  });

  it('reads an absent key as on, not as off', () => {
    // A client talking to a server that predates the switch must not go
    // bare; `undefined` is "the app never said", which is the picture.
    withConfig({ name: 'Assistant' });

    render(<WelcomeScreen {...props} />);

    expect(screen.getByText('logo')).toBeInTheDocument();
  });
});

describe('the welcome screen’s composer', () => {
  it('asks for the pill whatever the viewport is', () => {
    withConfig({}, [PROFILE], 'Entry');

    render(<WelcomeScreen {...props} />);

    expect(mockComposerLayout).toHaveBeenCalledWith('pill');
  });
});
