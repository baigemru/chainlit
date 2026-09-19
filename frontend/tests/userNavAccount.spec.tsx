import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UserNav from '@/components/header/UserNav';

/**
 * The user menu is a destination list, and it now has exactly one destination
 * besides the door out. `user_menu_links` is gone: a config that still carries
 * the array — every deployed `config.toml` does — must not grow rows back.
 */

const mockUseAuth = vi.fn();
const mockUseConfig = vi.fn();
const mockNavigate = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useAuth: () => mockUseAuth(),
  useConfig: () => mockUseConfig()
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate
}));

// Radix opens its content through a portal on `pointerdown` and its popper
// wants a `ResizeObserver` jsdom has not got. Passthroughs, so what is under
// test is UserNav's own rows rather than a menu library's mounting rules.
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: any) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: any) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: any) => <div>{children}</div>,
  DropdownMenuLabel: ({ children }: any) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuItem: ({ children, onClick }: any) => (
    <div role="menuitem" onClick={onClick}>
      {children}
    </div>
  )
}));

vi.mock('@/components/i18n/Translator', () => ({
  default: ({ path }: { path: string }) => <span>{path}</span>
}));

const someone = {
  id: 'u-1',
  identifier: 'someone@example.com',
  display_name: 'Someone',
  metadata: { image: 'https://example.com/a.png' }
};

const configure = (ui: Record<string, unknown>) =>
  mockUseConfig.mockReturnValue({ config: { ui, chatProfiles: [] } });

const rows = () => screen.queryAllByRole('menuitem');

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: someone, logout: vi.fn() });
  configure({});
});

describe('the user menu', () => {
  it('offers nothing but logout when the app declared no account page', () => {
    render(<UserNav />);

    expect(
      screen.queryByText('navigation.user.menu.account')
    ).not.toBeInTheDocument();
    expect(rows()).toHaveLength(1);
    expect(screen.getByText('navigation.user.menu.logout')).toBeInTheDocument();
  });

  it('keeps the row away while the account page is switched off', () => {
    configure({ account: { enabled: false, title: '' } });

    render(<UserNav />);

    expect(
      screen.queryByText('navigation.user.menu.account')
    ).not.toBeInTheDocument();
  });

  it('names the row with the translation when the app configured no title', () => {
    configure({ account: { enabled: true, title: '' } });

    render(<UserNav />);

    expect(
      screen.getByText('navigation.user.menu.account')
    ).toBeInTheDocument();
    expect(rows()).toHaveLength(2);
  });

  it('prefers the configured title over the translation', () => {
    configure({ account: { enabled: true, title: 'Кабинет' } });

    render(<UserNav />);

    expect(screen.getByText('Кабинет')).toBeInTheDocument();
    expect(
      screen.queryByText('navigation.user.menu.account')
    ).not.toBeInTheDocument();
  });

  it('sends the row to the account page and nowhere else', () => {
    configure({ account: { enabled: true, title: '' } });

    render(<UserNav />);
    fireEvent.click(screen.getByText('navigation.user.menu.account'));

    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/account');
  });

  it('renders the same rows in the phone overflow menu', () => {
    configure({ account: { enabled: true, title: '' } });

    render(<UserNav collapsed />);

    // Collapsed is the same fragment without the avatar trigger around it;
    // the rows are the point, the button is what must be absent.
    expect(
      screen.getByText('navigation.user.menu.account')
    ).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows the user behind an avatar when it is not collapsed', () => {
    render(<UserNav />);

    expect(screen.getByText('Someone')).toBeInTheDocument();
    expect(screen.getByRole('button')).toHaveAttribute('id', 'user-nav-button');
  });

  it('ignores a user_menu_links array a deployed config still carries', () => {
    configure({
      account: { enabled: true, title: '' },
      user_menu_links: [
        { name: 'Lago', url: '/billing/portal' },
        { name: 'Docs', url: 'https://example.com' }
      ]
    });

    render(<UserNav />);

    expect(screen.queryByText('Lago')).not.toBeInTheDocument();
    expect(screen.queryByText('Docs')).not.toBeInTheDocument();
    expect(rows()).toHaveLength(2);
  });
});
