import { render, screen } from '@testing-library/react';
import { createContext } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChatProfile } from '@chainlit/react-client';

import WelcomeScreen from '@/components/chat/WelcomeScreen';

const mockUseConfig = vi.fn();
const mockUseChatSession = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  MOBILE_BREAKPOINT: 768,
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path
  }),
  useChatMessages: () => ({ messages: [] }),
  useChatSession: () => mockUseChatSession(),
  useConfig: () => mockUseConfig()
}));

// Everything around the hint is a name in the document: what is under test
// is whether the line is there and whose words it is.
vi.mock('@/components/chat/MessageComposer', () => ({
  default: () => <div>composer</div>
}));
vi.mock('@/components/chat/Starters', () => ({
  default: () => <div>starters</div>
}));
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

const withProfiles = (profiles: ChatProfile[], current?: string) => {
  mockUseConfig.mockReturnValue({ config: { chatProfiles: profiles } });
  mockUseChatSession.mockReturnValue({ chatProfile: current });
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('the composer hint', () => {
  it('shows the current profile’s hint under the composer', () => {
    withProfiles(
      [profile('Entry', { composer_hint: 'Enter is a quick search' })],
      'Entry'
    );

    render(<WelcomeScreen {...props} />);

    expect(screen.getByText('Enter is a quick search')).toBeInTheDocument();
  });

  it('draws nothing when the profile set no hint', () => {
    withProfiles([profile('Entry')], 'Entry');

    const { container } = render(<WelcomeScreen {...props} />);

    expect(container.querySelector('.composer-hint')).toBeNull();
  });

  it('never borrows another profile’s hint', () => {
    withProfiles(
      [
        profile('Entry', { composer_hint: 'Enter is a quick search' }),
        profile('Buyer', { composer_hint: 'send me a photo' })
      ],
      'Buyer'
    );

    render(<WelcomeScreen {...props} />);

    expect(screen.getByText('send me a photo')).toBeInTheDocument();
    expect(
      screen.queryByText('Enter is a quick search')
    ).not.toBeInTheDocument();
  });

  it('draws nothing before a profile is settled', () => {
    withProfiles([profile('Entry', { composer_hint: 'a hint' })], undefined);

    const { container } = render(<WelcomeScreen {...props} />);

    expect(container.querySelector('.composer-hint')).toBeNull();
  });
});
