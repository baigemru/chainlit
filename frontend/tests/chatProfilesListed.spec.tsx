import { render as renderBase, screen } from '@testing-library/react';
import { type ReactElement, createContext } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChatProfile } from '@chainlit/react-client';

import ChatProfiles from '@/components/header/ChatProfiles';

const mockUseConfig = vi.fn();
const mockUseChatSession = vi.fn();
const mockSetChatProfile = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  MOBILE_BREAKPOINT: 768,
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path
  }),
  useChatInteract: () => ({ clear: vi.fn() }),
  useChatMessages: () => ({ firstInteraction: undefined }),
  useChatSession: () => mockUseChatSession(),
  useConfig: () => mockUseConfig()
}));

vi.mock('@/hooks/useParentThread', () => ({
  useResetKeptTranscript: () => vi.fn()
}));
vi.mock('@/components/header/NewChat', () => ({
  NewChatDialog: () => null
}));

const profile = (name: string, rest: Partial<ChatProfile> = {}): ChatProfile =>
  ({
    name,
    default: false,
    markdown_description: '',
    ...rest
  }) as ChatProfile;

const withProfiles = (profiles: ChatProfile[], current?: string) => {
  mockUseConfig.mockReturnValue({ config: { chatProfiles: profiles } });
  mockUseChatSession.mockReturnValue({
    chatProfile: current,
    setChatProfile: mockSetChatProfile
  });
};

// The component reaches for the attachments atom on a confirmed switch.
const render = (ui: ReactElement) => renderBase(<RecoilRoot>{ui}</RecoilRoot>);

const selector = () => document.querySelector('#chat-profiles');

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
});

describe('the profile selector', () => {
  it('appears when this device is offered more than one profile', () => {
    withProfiles([profile('Entry'), profile('Compare')], 'Entry');

    render(<ChatProfiles />);

    expect(selector()).not.toBeNull();
  });

  it('stays away when every other profile is an unlisted door', () => {
    // The entry profile plus two doors a starter walks through is still one
    // choice, and one door is not a selector.
    withProfiles(
      [
        profile('Entry'),
        profile('Buyer', { listed: false }),
        profile('Expand', { listed: false })
      ],
      'Entry'
    );

    render(<ChatProfiles />);

    expect(selector()).toBeNull();
  });

  it('stays away while the user is standing inside an unlisted door', () => {
    // The count is over what is *offered*. Counting what is on screen would
    // conjure a two-item selector out of the one entry point plus wherever a
    // starter has just taken the user.
    withProfiles(
      [profile('Entry'), profile('Buyer', { listed: false })],
      'Buyer'
    );

    render(<ChatProfiles />);

    expect(selector()).toBeNull();
  });

  it('still names the unlisted profile the user is in', () => {
    withProfiles(
      [
        profile('Entry'),
        profile('Compare'),
        profile('Buyer', { listed: false, display_name: 'Buyer’s eyes' })
      ],
      'Buyer'
    );

    render(<ChatProfiles />);

    expect(selector()).not.toBeNull();
    expect(screen.getByText('Buyer’s eyes')).toBeInTheDocument();
  });

  it('replaces a vanished profile through the one default rule', () => {
    // The config was rewritten under a live session: the profile it named is
    // gone. The replacement must be where a fresh chat would open, not
    // whatever the config happens to begin with — which here is a door.
    withProfiles(
      [
        profile('Buyer', { listed: false }),
        profile('Entry', { default: true }),
        profile('Compare')
      ],
      'Renamed'
    );

    render(<ChatProfiles />);

    expect(mockSetChatProfile).toHaveBeenCalledWith('Entry');
  });

  it('does not reach for the other device’s profile either', () => {
    window.sessionStorage.setItem('chainlit_device_override', 'mobile');
    withProfiles(
      [
        profile('Desk', { device: 'pc', default: true }),
        profile('Phone', { device: 'mobile' }),
        profile('Phone two', { device: 'mobile' })
      ],
      'Renamed'
    );

    render(<ChatProfiles />);

    expect(mockSetChatProfile).toHaveBeenCalledWith('Phone');
  });

  it('leaves a profile that still exists alone', () => {
    withProfiles([profile('Entry'), profile('Compare')], 'Compare');

    render(<ChatProfiles />);

    expect(mockSetChatProfile).not.toHaveBeenCalled();
  });

  it('does not count the other device’s profiles either', () => {
    window.sessionStorage.setItem('chainlit_device_override', 'mobile');
    withProfiles(
      [
        profile('Entry', { device: 'mobile' }),
        profile('Desk', { device: 'pc' })
      ],
      'Entry'
    );

    render(<ChatProfiles />);

    expect(selector()).toBeNull();
  });
});
