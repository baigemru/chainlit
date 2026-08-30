import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MobileNotice, {
  resetMobileNoticeForTests
} from '@/components/MobileNotice';

const mockUseConfig = vi.fn();
const mockUseDeviceKey = vi.fn();
const mockToast = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useConfig: () => mockUseConfig()
}));

vi.mock('@/hooks/use-mobile', () => ({
  useDeviceKey: () => mockUseDeviceKey()
}));

vi.mock('sonner', () => ({
  toast: (...args: unknown[]) => mockToast(...args)
}));

const SEEN_KEY = 'chainlit_mobile_notice_seen';

const NOTICE = {
  enabled: true,
  mode: 'dialog' as const,
  title: 'Full version',
  text: 'The full application is available on a computer.',
  link_url: '/?device=pc',
  link_label: 'Open the full version',
  dismiss_label: 'Stay here',
  frequency: 'session' as const
};

const configure = (notice?: Partial<typeof NOTICE> | null) =>
  mockUseConfig.mockReturnValue({
    config: {
      ui: notice === null ? {} : { mobile_notice: { ...NOTICE, ...notice } },
      chatProfiles: []
    }
  });

/**
 * Node 25 carries its own `localStorage` on `globalThis`, so vitest 0.34's
 * `populateGlobal` treats the key as already present and skips it: jsdom's
 * real Storage never reaches the test global, and what is left has no
 * `getItem` at all. The component guards every storage touch, so it would
 * survive that silently and every assertion below would pass for the wrong
 * reason — the spec therefore brings a storage of its own.
 */
const fakeStorage = (): Storage => {
  const entries = new Map<string, string>();
  return {
    get length() {
      return entries.size;
    },
    clear: () => entries.clear(),
    getItem: (key: string) => entries.get(key) ?? null,
    key: (index: number) => Array.from(entries.keys())[index] ?? null,
    removeItem: (key: string) => void entries.delete(key),
    setItem: (key: string, value: string) => void entries.set(key, value)
  };
};

beforeEach(() => {
  vi.clearAllMocks();
  // The once-per-load flag is module state; a case that inherited it from
  // its predecessor would pass every "shows nothing" assertion for the wrong
  // reason.
  resetMobileNoticeForTests();
  for (const name of ['localStorage', 'sessionStorage'] as const) {
    Object.defineProperty(window, name, {
      value: fakeStorage(),
      configurable: true,
      writable: true
    });
  }
  mockUseDeviceKey.mockReturnValue('mobile');
});

describe('MobileNotice', () => {
  it('shows nothing when the app did not ask for it', () => {
    configure(null);

    render(<MobileNotice />);

    expect(screen.queryByText(NOTICE.text)).not.toBeInTheDocument();
    expect(mockToast).not.toHaveBeenCalled();
  });

  it('shows nothing when the notice is disabled', () => {
    configure({ enabled: false });

    render(<MobileNotice />);

    expect(screen.queryByText(NOTICE.text)).not.toBeInTheDocument();
  });

  it('shows nothing on a desktop', () => {
    configure();
    mockUseDeviceKey.mockReturnValue('pc');

    render(<MobileNotice />);

    expect(screen.queryByText(NOTICE.text)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(SEEN_KEY)).toBeNull();
  });

  it('renders the configured words on a phone', () => {
    configure();

    render(<MobileNotice />);

    expect(screen.getByText(NOTICE.title)).toBeInTheDocument();
    expect(screen.getByText(NOTICE.text)).toBeInTheDocument();
    expect(screen.getByText(NOTICE.link_label)).toBeInTheDocument();
    expect(screen.getByText(NOTICE.dismiss_label)).toBeInTheDocument();
  });

  it('uses the text as the heading when no title is configured', () => {
    configure({ title: '' });

    render(<MobileNotice />);

    // One node, not two: the heading is the text, and repeating it as a
    // description would have a screen reader read the sentence twice.
    expect(screen.getAllByText(NOTICE.text)).toHaveLength(1);
  });

  it('remembers a dismissal for the tab', () => {
    configure();

    render(<MobileNotice />);
    fireEvent.click(screen.getByText(NOTICE.dismiss_label));

    expect(screen.queryByText(NOTICE.text)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(SEEN_KEY)).toBe('1');
    expect(window.localStorage.getItem(SEEN_KEY)).toBeNull();
  });

  it('stays away for the rest of the page load', () => {
    configure();

    const first = render(<MobileNotice />);
    expect(screen.getByText(NOTICE.text)).toBeInTheDocument();
    first.unmount();

    // `Page` remounts on every Home <-> Thread navigation; deliberately no
    // reset here, because the browser would not have reloaded either.
    render(<MobileNotice />);

    expect(screen.queryByText(NOTICE.text)).not.toBeInTheDocument();
  });

  it('honours a dismissal already in storage', () => {
    configure();
    window.sessionStorage.setItem(SEEN_KEY, '1');

    render(<MobileNotice />);

    expect(screen.queryByText(NOTICE.text)).not.toBeInTheDocument();
  });

  it('asks again on the next load when the frequency is always', () => {
    configure({ frequency: 'always' });

    const first = render(<MobileNotice />);
    fireEvent.click(screen.getByText(NOTICE.dismiss_label));
    first.unmount();

    // "always" is per page load, so a reload — and only a reload — brings it
    // back; nothing was written to either storage.
    expect(window.sessionStorage.getItem(SEEN_KEY)).toBeNull();
    expect(window.localStorage.getItem(SEEN_KEY)).toBeNull();
    resetMobileNoticeForTests();
    render(<MobileNotice />);

    expect(screen.getByText(NOTICE.text)).toBeInTheDocument();
  });

  it('remembers a dismissal for the browser when the frequency is once', () => {
    configure({ frequency: 'once' });

    render(<MobileNotice />);
    fireEvent.click(screen.getByText(NOTICE.dismiss_label));

    expect(window.localStorage.getItem(SEEN_KEY)).toBe('1');
    expect(window.sessionStorage.getItem(SEEN_KEY)).toBeNull();
  });

  it('ignores a dismissal filed under another frequency', () => {
    configure({ frequency: 'once' });
    window.sessionStorage.setItem(SEEN_KEY, '1');

    render(<MobileNotice />);

    expect(screen.getByText(NOTICE.text)).toBeInTheDocument();
  });

  it('offers the link through a toast in toast mode', () => {
    configure({ mode: 'toast' });

    const { container } = render(<MobileNotice />);

    expect(container).toBeEmptyDOMElement();
    expect(mockToast).toHaveBeenCalledTimes(1);
    const [message, options] = mockToast.mock.calls[0];
    expect(message).toBe(NOTICE.text);
    expect(options.action.label).toBe(NOTICE.link_label);
    expect(options.duration).toBe(10000);
    // Showing the toast is the whole offer: it is spent even if nobody
    // touches it.
    expect(window.sessionStorage.getItem(SEEN_KEY)).toBe('1');
  });

  it('does not raise a toast twice in one page load', () => {
    configure({ mode: 'toast', frequency: 'always' });

    const first = render(<MobileNotice />);
    first.unmount();
    render(<MobileNotice />);

    expect(mockToast).toHaveBeenCalledTimes(1);
  });
});
