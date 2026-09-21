import {
  fireEvent,
  render as renderBase,
  screen
} from '@testing-library/react';
import { ReactElement } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import NewChatButton from '@/components/header/NewChat';

const mockClear = vi.fn();
const mockUseConfig = vi.fn();
const mockSetOpenMobile = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  // `hooks/use-mobile` reads the breakpoint from the package, so that the
  // layout and the element panel's mobile rule cannot drift apart. A mock
  // of the whole module has to carry it.
  MOBILE_BREAKPOINT: 768,
  useChatInteract: () => ({ clear: mockClear }),
  useConfig: () => mockUseConfig()
}));

// The button also renders inside the mobile sheet, so it reaches for the
// sidebar; here it renders bare, where there is no provider to reach.
vi.mock('@/components/ui/sidebar', () => ({
  useSidebar: () => ({ setOpenMobile: mockSetOpenMobile })
}));

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));

// The button resets the kept-transcript atoms on confirm, so it needs a
// Recoil root around it.
const render = (ui: ReactElement) => renderBase(<RecoilRoot>{ui}</RecoilRoot>);

describe('NewChatButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseConfig.mockReturnValue({ config: {} });
  });

  it('renders the button correctly', () => {
    render(<NewChatButton />);
    const button = screen.getByRole('button');
    expect(button).toBeInTheDocument();
    expect(button.querySelector('svg')).toBeInTheDocument();
  });

  it('opens dialog by default when config is undefined', () => {
    mockUseConfig.mockReturnValue({ config: undefined });

    render(<NewChatButton />);
    fireEvent.click(screen.getByRole('button'));

    expect(
      screen.getByText('navigation.newChat.dialog.title')
    ).toBeInTheDocument();
  });

  it('opens dialog by default when ui config is missing', () => {
    mockUseConfig.mockReturnValue({ config: { project: {} } });

    render(<NewChatButton />);
    fireEvent.click(screen.getByRole('button'));

    expect(
      screen.getByText('navigation.newChat.dialog.title')
    ).toBeInTheDocument();
  });

  it('clears chat and navigates when confirmed via Dialog', () => {
    mockUseConfig.mockReturnValue({ config: {} });
    const mockNavigate = vi.fn();

    render(<NewChatButton navigate={mockNavigate} />);

    fireEvent.click(screen.getByRole('button'));

    const confirmBtn = screen.getByText('common.actions.confirm');
    fireEvent.click(confirmBtn);

    expect(mockClear).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('skips dialog and activates immediately when confirm_new_chat is false', () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { confirm_new_chat: false } }
    });

    const mockNavigate = vi.fn();
    render(<NewChatButton navigate={mockNavigate} />);

    fireEvent.click(screen.getByRole('button'));

    expect(
      screen.queryByText('navigation.newChat.dialog.title')
    ).not.toBeInTheDocument();
    expect(mockClear).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('opens dialog explicitly when confirm_new_chat is true', () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { confirm_new_chat: true } }
    });

    render(<NewChatButton />);
    fireEvent.click(screen.getByRole('button'));

    expect(
      screen.getByText('navigation.newChat.dialog.title')
    ).toBeInTheDocument();
    expect(mockClear).not.toHaveBeenCalled();
  });

  it('closes the mobile sheet it may have been pressed in', () => {
    // A new chat from `/` leaves the address where it was, so the sheet's own
    // effect on the pathname never fires: this is the one site that has to
    // close it by hand.
    mockUseConfig.mockReturnValue({
      config: { ui: { confirm_new_chat: false } }
    });

    render(<NewChatButton navigate={vi.fn()} />);
    fireEvent.click(screen.getByRole('button'));

    expect(mockSetOpenMobile).toHaveBeenCalledWith(false);
  });

  it('says what it does when the sidebar asks for the wide one', () => {
    // In the header the button is one icon among six and has no room to say
    // so; in the sidebar it is what the panel is opened for, and an icon
    // there is a guess the user has to make.
    render(<NewChatButton wide />);

    const button = screen.getByRole('button');
    expect(button.textContent).toContain('navigation.newChat.button');
    expect(button.className).toContain('w-full');
    expect(button.querySelector('svg')).toBeInTheDocument();
  });

  it('keeps the header copy an icon', () => {
    render(<NewChatButton />);

    const button = screen.getByRole('button');
    expect(button.textContent).not.toContain('navigation.newChat.button');
    expect(button.className).not.toContain('w-full');
  });

  it('uses custom onConfirm handler if provided', () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { confirm_new_chat: false } }
    });

    const customOnConfirm = vi.fn();
    render(<NewChatButton onConfirm={customOnConfirm} />);

    fireEvent.click(screen.getByRole('button'));

    expect(customOnConfirm).toHaveBeenCalledTimes(1);
    expect(mockClear).not.toHaveBeenCalled();
  });
});
