import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AccountFooter from '@/components/LeftSidebar/AccountFooter';
import { SidebarProvider } from '@/components/ui/sidebar';

/**
 * The row at the foot of the left panel that leads to the account page.
 *
 * What it owns is a claim about when it exists and where it goes: the same
 * two conditions the user menu's row answers to, the page itself rather than
 * one of its sections, and the badge the header's avatar already carries. The
 * router is real — `Link` and `useLocation` are the subject — and only the
 * config, the user and the pushed count are stood in for.
 */

const mockUseConfig = vi.fn();
const mockUseAuth = vi.fn();
const mockBadge = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useConfig: () => mockUseConfig(),
  useAuth: () => mockUseAuth(),
  // Handed straight to `useRecoilValue`, which is answered below; a real
  // atom would buy nothing but a RecoilRoot to hold it.
  accountBadgeState: { key: 'AccountBadge' }
}));

vi.mock('recoil', () => ({
  useRecoilValue: () => mockBadge()
}));

// Desktop width: on a phone `Sidebar` is a Radix sheet, and this row is the
// same row either way.
vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => false
}));

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));
vi.mock('@/components/i18n/Translator', () => ({
  default: ({ path }: { path: string }) => <span>{path}</span>,
  useTranslation: () => ({ t: (path: string) => path })
}));

const someone = {
  id: 'u-1',
  identifier: 'someone@example.com',
  metadata: {}
};

const configure = (account?: Record<string, unknown>) =>
  mockUseConfig.mockReturnValue({ config: { ui: { account } } });

const mount = (at = '/') =>
  render(
    <MemoryRouter initialEntries={[at]}>
      <SidebarProvider>
        <AccountFooter />
      </SidebarProvider>
    </MemoryRouter>
  );

const row = () => screen.queryByRole('link');
const dot = () => screen.queryByLabelText('account.badge.aria');

/**
 * `SidebarProvider`'s own wrapper is always in the container, so "nothing" is
 * the wrapper standing empty — which is also the claim that matters: no
 * footer, and so none of the footer's padding under the history.
 */
const nothing = (container: HTMLElement) =>
  expect(container.firstElementChild).toBeEmptyDOMElement();

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: someone });
  mockBadge.mockReturnValue(undefined);
  configure({ enabled: true, title: '' });
});

describe('the account row at the foot of the sidebar', () => {
  it('leads to the account page', () => {
    mount();

    expect(row()!.getAttribute('href')).toBe('/account');
  });

  it('is the panel’s footer, not another row of the history', () => {
    const { container } = mount();

    const footer = container.querySelector('[data-sidebar="footer"]');
    expect(footer).not.toBeNull();
    expect(footer!.contains(row()!)).toBe(true);
  });

  it('names itself from the translation when the app titled nothing', () => {
    mount();

    expect(
      screen.getByText('navigation.user.menu.account')
    ).toBeInTheDocument();
  });

  it('takes the application’s own title when it set one', () => {
    configure({ enabled: true, title: 'Личный кабинет' });

    mount();

    expect(screen.getByText('Личный кабинет')).toBeInTheDocument();
    expect(
      screen.queryByText('navigation.user.menu.account')
    ).not.toBeInTheDocument();
  });

  it('shows the user’s initial when there is no picture of them', () => {
    mount();

    expect(screen.getByText('S')).toBeInTheDocument();
  });

  it('lights up on the account page', () => {
    mount('/account');

    expect(row()!.getAttribute('data-active')).toBe('true');
  });

  it('lights up for any section of it', () => {
    // Unlike the pinned rows above, this one claims the page and not a tab,
    // so a `?tab=` decides nothing here.
    mount('/account?tab=feed');

    expect(row()!.getAttribute('data-active')).toBe('true');
  });

  it('stays unlit behind a chat', () => {
    mount('/thread/a');

    expect(row()!.getAttribute('data-active')).toBe('false');
  });

  it('carries the badge the header’s avatar carries', () => {
    mockBadge.mockReturnValue(3);

    mount();

    expect(dot()).toBeInTheDocument();
  });

  it('marks nothing for a count of zero or none at all', () => {
    mockBadge.mockReturnValue(0);
    const { unmount } = mount();
    expect(dot()).not.toBeInTheDocument();
    unmount();

    mockBadge.mockReturnValue(undefined);
    mount();
    expect(dot()).not.toBeInTheDocument();
  });

  it('renders nothing when the application declared no account page', () => {
    configure(undefined);

    const { container } = mount();

    expect(row()).toBeNull();
    nothing(container);
  });

  it('renders nothing when the account page is declared but switched off', () => {
    configure({ enabled: false, title: '' });

    const { container } = mount();

    nothing(container);
  });

  it('renders nothing when nobody is signed in', () => {
    // No user, no account of their own — and no face to draw either.
    mockUseAuth.mockReturnValue({ user: null });

    const { container } = mount();

    nothing(container);
  });
});
