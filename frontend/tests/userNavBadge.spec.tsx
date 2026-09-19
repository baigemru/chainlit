import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UserNav from '@/components/header/UserNav';

/**
 * The badge the server pushes, on the avatar and in the menu.
 *
 * `accountBadgeState` is a projection of `account.badge`, and `undefined`
 * means the application registered no badge hook — so nothing renders. The
 * count is whatever the hook said: the client never invents a zero, and
 * opening the page does not blank it.
 */

const mockBadge = vi.fn();
const mockNavigate = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useAuth: () => ({
    user: { id: 'u-1', identifier: 'someone@example.com' },
    logout: vi.fn()
  }),
  useConfig: () => ({
    config: { ui: { account: { enabled: true, title: '' } }, chatProfiles: [] }
  }),
  // Handed straight to `useRecoilValue`, which is answered below; a real
  // atom would buy nothing but a RecoilRoot to hold it.
  accountBadgeState: { key: 'AccountBadge' }
}));

vi.mock('recoil', () => ({
  useRecoilValue: () => mockBadge()
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate
}));

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
  default: ({ path }: { path: string }) => <span>{path}</span>,
  useTranslation: () => ({ t: (path: string) => path })
}));

const dot = () => screen.queryByLabelText('account.badge.aria');

beforeEach(() => {
  vi.clearAllMocks();
  mockBadge.mockReturnValue(undefined);
});

describe('the account badge in the user menu', () => {
  it('shows nothing until the server has said something', () => {
    render(<UserNav />);

    expect(dot()).not.toBeInTheDocument();
    expect(screen.queryByText('3')).not.toBeInTheDocument();
  });

  it('shows nothing for a count of zero', () => {
    // Nothing unseen is not a notification; the dot would be a lie and the
    // "0" beside the row would be noise.
    mockBadge.mockReturnValue(0);

    render(<UserNav />);

    expect(dot()).not.toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('marks the avatar and counts on the row', () => {
    mockBadge.mockReturnValue(3);

    render(<UserNav />);

    // The menu is closed most of the time, so the dot is what carries the
    // news; the number belongs beside the destination it is about.
    expect(dot()).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('counts on the row in the phone overflow menu too', () => {
    mockBadge.mockReturnValue(12);

    render(<UserNav collapsed />);

    // No avatar to mark when the rows are rendered into the header's own
    // menu -- the count is the whole signal there.
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(dot()).not.toBeInTheDocument();
  });
});
