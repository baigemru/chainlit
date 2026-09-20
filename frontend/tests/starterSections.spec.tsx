import { fireEvent, render, screen } from '@testing-library/react';
import { createContext } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { IStarter, IStarterCategory } from '@chainlit/react-client';

import Starter from '@/components/chat/Starter';
import Starters from '@/components/chat/Starters';

const mockUseConfig = vi.fn();
const mockSendMessage = vi.fn();
const mockClear = vi.fn();
const mockSetChatProfile = vi.fn();
const mockNavigate = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  // `hooks/use-mobile` reads the breakpoint from the package, so that the
  // layout and the element panel's mobile rule cannot drift apart. A mock
  // of the whole module has to carry it.
  MOBILE_BREAKPOINT: 768,
  // As a deployment under `--root-path` builds it.
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => `/app${path}`
  }),
  useAuth: () => ({ user: { identifier: 'someone' } }),
  useChatData: () => ({ loading: false, connected: true }),
  useChatInteract: () => ({ sendMessage: mockSendMessage, clear: mockClear }),
  useChatSession: () => ({
    chatProfile: undefined,
    setChatProfile: mockSetChatProfile
  }),
  useConfig: () => mockUseConfig()
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));

const starter = (label: string, rest: Partial<IStarter> = {}): IStarter => ({
  label,
  message: label,
  ...rest
});

const category = (
  label: string,
  rest: Partial<IStarterCategory> = {}
): IStarterCategory => ({
  label,
  starters: [starter(`${label} one`)],
  ...rest
});

const withCategories = (categories: IStarterCategory[]) =>
  mockUseConfig.mockReturnValue({
    config: { chatProfiles: [], starterCategories: categories }
  });

const pinDevice = (device: string) =>
  window.sessionStorage.setItem('chainlit_device_override', device);

const renderStarter = (s: IStarter, layout?: 'tiles' | 'plates' | 'rows') =>
  render(
    <RecoilRoot>
      <Starter starter={s} layout={layout} />
    </RecoilRoot>
  );

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
});

describe('starter sections', () => {
  it('draws every category, in the order the server sent them', () => {
    withCategories([
      category('Quick'),
      category('Scenarios'),
      category('Tasks')
    ]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    const headings = Array.from(
      container.querySelectorAll('[data-test^="starter-category-"]')
    ).map((node) => node.getAttribute('data-test'));
    expect(headings).toEqual([
      'starter-category-Quick',
      'starter-category-Scenarios',
      'starter-category-Tasks'
    ]);
  });

  it('shows the starters of every section at once, unasked', () => {
    // The pills had to be pressed before they said anything. Nothing here is
    // pressed: both sections are readable on arrival.
    withCategories([
      { label: 'Quick', starters: [starter('Find it')] },
      { label: 'Scenarios', starters: [starter('Buy it')] }
    ]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getByText('Find it')).toBeInTheDocument();
    expect(screen.getByText('Buy it')).toBeInTheDocument();
  });

  it('carries the section description under its heading', () => {
    withCategories([category('Quick', { description: 'the same as Enter' })]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getByText('the same as Enter')).toBeInTheDocument();
  });

  it('drops a section the device filter emptied', () => {
    pinDevice('mobile');
    withCategories([
      { label: 'Shared', starters: [starter('Anywhere')] },
      { label: 'Desk', starters: [starter('Spreadsheet', { device: 'pc' })] }
    ]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getByText('Shared')).toBeInTheDocument();
    expect(screen.queryByText('Desk')).not.toBeInTheDocument();
  });

  it('renders a plate with its description and caption', () => {
    withCategories([
      {
        label: 'Scenarios',
        layout: 'plates',
        starters: [
          starter('Buyer', {
            description: 'a shortlist and a report',
            caption: '~3 min'
          })
        ]
      }
    ]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getByText('a shortlist and a report')).toBeInTheDocument();
    expect(screen.getByText('~3 min')).toBeInTheDocument();
  });

  it('renders a row with its description and caption', () => {
    withCategories([
      {
        label: 'Tasks',
        layout: 'rows',
        starters: [
          starter('Check a supplier', {
            description: 'trust score',
            caption: 'free'
          })
        ]
      }
    ]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getByText('trust score')).toBeInTheDocument();
    expect(screen.getByText('free')).toBeInTheDocument();
  });

  it('falls back to tiles for a layout it does not know', () => {
    // A newer backend naming a density this build has never heard of must
    // still draw the offers, not swallow them.
    withCategories([
      {
        label: 'Quick',
        layout: 'carousel' as any,
        starters: [starter('Find it', { description: 'never drawn as a tile' })]
      }
    ]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getByText('Find it')).toBeInTheDocument();
    expect(screen.queryByText('never drawn as a tile')).not.toBeInTheDocument();
  });
});

describe('icons and keys', () => {
  it('sends a section icon out of /public through the API client', () => {
    // Under a `--root-path` a bare `/public/x.png` resolves against the wrong
    // prefix and the icon silently does not load.
    withCategories([category('Quick', { icon: '/public/quick.png' })]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(container.querySelector('section img')).toHaveAttribute(
      'src',
      '/app/public/quick.png'
    );
  });

  it('leaves an absolute section icon alone', () => {
    withCategories([category('Quick', { icon: 'https://cdn.test/q.png' })]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(container.querySelector('section img')).toHaveAttribute(
      'src',
      'https://cdn.test/q.png'
    );
  });

  it('resolves a starter icon the same way', () => {
    renderStarter(starter('Find it', { icon: '/public/find.png' }));

    expect(screen.getByRole('img')).toHaveAttribute(
      'src',
      '/app/public/find.png'
    );
  });

  it('draws both starters when two in a section share a label', () => {
    // The label is not an identity: keyed on it, React reconciles the pair
    // into one button and the second offer disappears.
    const warn = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    withCategories([
      {
        label: 'Tasks',
        starters: [
          starter('Check it', { profile: 'ENTITY' }),
          starter('Check it', { profile: 'FACTORY' })
        ]
      }
    ]);

    render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(screen.getAllByText('Check it')).toHaveLength(2);
    // And without React complaining about a duplicate key on the way.
    expect(
      warn.mock.calls.some((call) => String(call[0]).includes('same key'))
    ).toBe(false);
    warn.mockRestore();
  });
});

describe('a collapsible section', () => {
  it('is open on a desktop', () => {
    pinDevice('pc');
    withCategories([category('Tasks', { collapsible: true })]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    const details = container.querySelector('details')!;
    expect(details).toBeInTheDocument();
    expect(details.open).toBe(true);
  });

  it('is folded on a phone', () => {
    pinDevice('mobile');
    withCategories([category('Tasks', { collapsible: true })]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(container.querySelector('details')!.open).toBe(false);
  });

  it('counts the starters this device can see in its summary', () => {
    pinDevice('mobile');
    withCategories([
      {
        label: 'Tasks',
        collapsible: true,
        starters: [
          starter('Here'),
          starter('Also here'),
          starter('Elsewhere', { device: 'pc' })
        ]
      }
    ]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(container.querySelector('summary')!.textContent).toContain('· 2');
  });

  it('leaves an ordinary section with nothing to fold', () => {
    withCategories([category('Quick')]);

    const { container } = render(
      <RecoilRoot>
        <Starters />
      </RecoilRoot>
    );

    expect(container.querySelector('details')).toBeNull();
  });
});

describe('what a starter does when pressed', () => {
  it('sends its message', () => {
    renderStarter(starter('Find it'));
    fireEvent.click(screen.getByRole('button'));

    expect(mockSendMessage).toHaveBeenCalledTimes(1);
    expect(mockSendMessage.mock.calls[0][0].output).toBe('Find it');
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('walks through a profile door without saying anything', () => {
    renderStarter(starter('Buyer', { profile: 'BUYER', message: '' }));
    fireEvent.click(screen.getByRole('button'));

    expect(mockSetChatProfile).toHaveBeenCalledWith('BUYER');
    expect(mockNavigate).toHaveBeenCalledWith('/');
    expect(mockSendMessage).not.toHaveBeenCalled();
  });

  it('navigates for an href and does nothing else', () => {
    // The account is a dialog over the live chat: tearing the session down
    // on the way there would blank the conversation behind it.
    renderStarter(
      starter('Subscribe', { href: '/account?tab=items', profile: 'BUYER' })
    );
    fireEvent.click(screen.getByRole('button'));

    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/account?tab=items');
    expect(mockSetChatProfile).not.toHaveBeenCalled();
    expect(mockClear).not.toHaveBeenCalled();
    expect(mockSendMessage).not.toHaveBeenCalled();
  });

  it('does nothing at all when the application disabled it', () => {
    renderStarter(
      starter('Subscribe', {
        disabled: true,
        href: '/account',
        profile: 'BUYER'
      })
    );
    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-disabled', 'true');

    fireEvent.click(button);

    expect(mockSendMessage).not.toHaveBeenCalled();
    expect(mockSetChatProfile).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('keeps a disabled starter on screen', () => {
    // Withdrawn silently, an offer is indistinguishable from one never made.
    renderStarter(starter('Subscribe', { disabled: true }));
    expect(screen.getByText('Subscribe')).toBeInTheDocument();
  });
});
