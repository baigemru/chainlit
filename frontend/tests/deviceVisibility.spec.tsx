import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChatProfile, IStarter } from '@chainlit/react-client';

import Starters from '@/components/chat/Starters';

import {
  getDeviceKey,
  matchesDevice,
  pickDefaultProfile
} from '@/hooks/use-mobile';

const mockUseConfig = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useChatSession: () => ({ chatProfile: undefined }),
  useConfig: () => mockUseConfig()
}));

// The starter itself pulls in the whole chat surface (transport, auth,
// attachments); the filtering is what is under test, so a stub that names
// itself is enough.
vi.mock('@/components/chat/Starter', () => ({
  default: ({ starter }: { starter: IStarter }) => <div>{starter.label}</div>
}));

const profile = (name: string, rest: Partial<ChatProfile> = {}): ChatProfile =>
  ({
    name,
    default: false,
    markdown_description: '',
    ...rest
  }) as ChatProfile;

const starter = (label: string, device?: IStarter['device']): IStarter => ({
  label,
  message: label,
  ...(device ? { device } : {})
});

const pinDevice = (device: string) =>
  window.sessionStorage.setItem('chainlit_device_override', device);

// jsdom ships no matchMedia, and the unpinned path subscribes to one. The
// stub only carries the subscription — the device itself is read from
// innerWidth, so the assertions still bite.
if (typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      media: query,
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {}
    })
  });
}

describe('matchesDevice', () => {
  it('shows an unlabelled offer to everyone', () => {
    expect(matchesDevice(undefined, 'mobile')).toBe(true);
    expect(matchesDevice(undefined, 'pc')).toBe(true);
  });

  it("shows an 'all' offer to everyone", () => {
    expect(matchesDevice('all', 'mobile')).toBe(true);
    expect(matchesDevice('all', 'pc')).toBe(true);
  });

  it('shows a labelled offer only on its own device', () => {
    expect(matchesDevice('mobile', 'mobile')).toBe(true);
    expect(matchesDevice('pc', 'pc')).toBe(true);
  });

  it('hides a labelled offer on the other device', () => {
    expect(matchesDevice('mobile', 'pc')).toBe(false);
    expect(matchesDevice('pc', 'mobile')).toBe(false);
  });

  it('shows an offer carrying a label it does not know', () => {
    expect(matchesDevice('tablet' as any, 'pc')).toBe(true);
    expect(matchesDevice('' as any, 'mobile')).toBe(true);
  });
});

describe('pickDefaultProfile', () => {
  it('takes the default among the profiles this device is offered', () => {
    const profiles = [
      profile('Hub PC', { device: 'pc', default: true }),
      profile('Search', { device: 'all' }),
      profile('Hub Mobile', { device: 'mobile', default: true })
    ];
    expect(pickDefaultProfile(profiles, 'mobile')).toBe('Hub Mobile');
    expect(pickDefaultProfile(profiles, 'pc')).toBe('Hub PC');
  });

  it("never takes the other device's default", () => {
    const profiles = [
      profile('Hub PC', { device: 'pc', default: true }),
      profile('Search', { device: 'mobile' })
    ];
    expect(pickDefaultProfile(profiles, 'mobile')).toBe('Search');
  });

  it('falls back to the first visible profile when none is marked', () => {
    const profiles = [
      profile('Hub PC', { device: 'pc' }),
      profile('Search', { device: 'mobile' }),
      profile('Compare', { device: 'mobile' })
    ];
    expect(pickDefaultProfile(profiles, 'mobile')).toBe('Search');
  });

  it('still names a profile when the device is offered none', () => {
    const profiles = [profile('Hub PC', { device: 'pc', default: true })];
    // Without a name App never opens the socket; a filtered-out config must
    // not leave the session profileless.
    expect(pickDefaultProfile(profiles, 'mobile')).toBe('Hub PC');
  });

  it('has nothing to name for an empty config', () => {
    expect(pickDefaultProfile([], 'pc')).toBeUndefined();
  });
});

describe('getDeviceKey', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState({}, '', '/');
  });

  it('reads the viewport when nothing is pinned', () => {
    // jsdom's default width is 1024.
    expect(getDeviceKey()).toBe('pc');
  });

  it('lets ?device= outrank the viewport and parks it for later', () => {
    window.history.replaceState({}, '', '/?device=mobile');
    expect(getDeviceKey()).toBe('mobile');

    window.history.replaceState({}, '', '/thread/abc');
    expect(getDeviceKey()).toBe('mobile');
  });

  it('ignores a value it does not understand', () => {
    window.history.replaceState({}, '', '/?device=watch');
    expect(getDeviceKey()).toBe('pc');
    expect(
      window.sessionStorage.getItem('chainlit_device_override')
    ).toBeNull();
  });
});

describe('Starters visibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    window.history.replaceState({}, '', '/');
  });

  it('offers a flat list filtered to this device', () => {
    pinDevice('mobile');
    mockUseConfig.mockReturnValue({
      config: {
        chatProfiles: [],
        starters: [
          starter('Everywhere'),
          starter('Phone only', 'mobile'),
          starter('Desk only', 'pc'),
          starter('All of them', 'all')
        ]
      }
    });

    render(<Starters />);

    expect(screen.getByText('Everywhere')).toBeInTheDocument();
    expect(screen.getByText('Phone only')).toBeInTheDocument();
    expect(screen.getByText('All of them')).toBeInTheDocument();
    expect(screen.queryByText('Desk only')).not.toBeInTheDocument();
  });

  it('falls back to the viewport when nothing is pinned', () => {
    // No override: this is the ordinary path, matchMedia subscription and
    // all, on jsdom's 1024px-wide window.
    mockUseConfig.mockReturnValue({
      config: {
        chatProfiles: [],
        starters: [starter('Phone only', 'mobile'), starter('Desk only', 'pc')]
      }
    });

    render(<Starters />);

    expect(screen.getByText('Desk only')).toBeInTheDocument();
    expect(screen.queryByText('Phone only')).not.toBeInTheDocument();
  });

  it('offers the same list to a pc the other way round', () => {
    pinDevice('pc');
    mockUseConfig.mockReturnValue({
      config: {
        chatProfiles: [],
        starters: [starter('Phone only', 'mobile'), starter('Desk only', 'pc')]
      }
    });

    render(<Starters />);

    expect(screen.getByText('Desk only')).toBeInTheDocument();
    expect(screen.queryByText('Phone only')).not.toBeInTheDocument();
  });

  it('renders nothing when every starter belongs to the other device', () => {
    pinDevice('mobile');
    mockUseConfig.mockReturnValue({
      config: { chatProfiles: [], starters: [starter('Desk only', 'pc')] }
    });

    const { container } = render(<Starters />);
    expect(container.querySelector('#starters')).toBeNull();
  });

  it('drops a category left empty by the filter', () => {
    pinDevice('mobile');
    mockUseConfig.mockReturnValue({
      config: {
        chatProfiles: [],
        starterCategories: [
          {
            label: 'Shared',
            starters: [starter('Anywhere'), starter('Desk only', 'pc')]
          },
          { label: 'Desk', starters: [starter('Spreadsheet', 'pc')] }
        ]
      }
    });

    render(<Starters />);

    expect(screen.getByText('Shared')).toBeInTheDocument();
    expect(screen.queryByText('Desk')).not.toBeInTheDocument();
  });

  it('shows only this device’s starters inside a chosen category', () => {
    pinDevice('mobile');
    mockUseConfig.mockReturnValue({
      config: {
        chatProfiles: [],
        starterCategories: [
          {
            label: 'Shared',
            starters: [starter('Anywhere'), starter('Desk only', 'pc')]
          }
        ]
      }
    });

    render(<Starters />);
    fireEvent.click(screen.getByText('Shared'));

    expect(screen.getByText('Anywhere')).toBeInTheDocument();
    expect(screen.queryByText('Desk only')).not.toBeInTheDocument();
  });
});
