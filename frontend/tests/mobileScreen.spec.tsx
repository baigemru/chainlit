import {
  MessageContext,
  defaultMessageContext
} from '@/contexts/MessageContext';
import { render, screen } from '@testing-library/react';
import { createContext } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IAsk, IStep } from '@chainlit/react-client';

import WaterMark from '@/components/WaterMark';
import { AskActionButtons } from '@/components/chat/Messages/Message/AskActionButtons';
import { MessageAvatar } from '@/components/chat/Messages/Message/Avatar';
import Step from '@/components/chat/Messages/Message/Step';
import {
  DEFAULT_MOBILE_HEADER,
  Header,
  staysInHeader
} from '@/components/header';

const mockUseConfig = vi.fn();
const mockUseAuth = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path
  }),
  useAuth: () => mockUseAuth(),
  useChatMessages: () => ({ messages: [], threadId: undefined }),
  useChatSession: () => ({ chatProfile: undefined }),
  useConfig: () => mockUseConfig()
}));

// The header's children are what it places, not what it decides; each one is
// a name in the document so the placement can be read off the render.
vi.mock('@/components/header/NewChat', () => ({
  default: () => <div>new_chat</div>
}));
vi.mock('@/components/header/ChatProfiles', () => ({
  default: () => <div>chat_profiles</div>
}));
vi.mock('@/components/header/Share', () => ({
  default: () => <div>share</div>
}));
vi.mock('@/components/header/Readme', () => ({
  default: () => <div>readme</div>
}));
vi.mock('@/components/header/ApiKeys', () => ({
  default: () => <div>api_keys</div>
}));
vi.mock('@/components/header/SidebarTrigger', () => ({
  default: () => <div>sidebar_trigger</div>
}));
vi.mock('@/components/header/ThemeToggle', () => ({
  ThemeToggle: () => <div>theme</div>
}));
vi.mock('@/components/header/UserNav', () => ({
  default: () => <div>user_nav</div>
}));
vi.mock('@/components/ButtonLink', () => ({
  default: ({ name }: { name?: string }) => <div>{name}</div>
}));
vi.mock('@/components/ui/sidebar', () => ({
  useSidebar: () => ({ open: false, openMobile: false, isMobile: false })
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => () => undefined }));

const WATERMARK = 'Large language models can make mistakes, check the sources';

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: () => WATERMARK }),
  default: ({ path }: { path: string }) => path
}));

/**
 * `useIsMobile` reads `innerWidth`, and jsdom's own default (1024) is the
 * desktop control case. Width, not the `device` label: these are layout
 * assertions.
 */
const setWidth = (width: number) => {
  Object.defineProperty(window, 'innerWidth', {
    value: width,
    configurable: true,
    writable: true
  });
};

const action = (id: string, label: string) =>
  ({ id, forId: 'm1', name: id, label }) as any;

const ask = (keys: string[]): IAsk =>
  ({
    spec: { stepId: 'm1', type: 'action', keys, timeout: 60 },
    callback: () => undefined,
    awaitingReply: false
  }) as unknown as IAsk;

const renderAsk = (actions: any[], keys: string[]) =>
  render(
    <MessageContext.Provider
      value={{ ...defaultMessageContext, askUser: ask(keys) }}
    >
      <AskActionButtons messageId="m1" actions={actions} />
    </MessageContext.Provider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockUseConfig.mockReturnValue({ config: { ui: {}, chatProfiles: [] } });
  mockUseAuth.mockReturnValue({ data: undefined, user: undefined });
});

afterEach(() => {
  setWidth(1024);
});

describe('staysInHeader', () => {
  it('keeps the three a phone needs when the app says nothing', () => {
    expect(DEFAULT_MOBILE_HEADER).toEqual([
      'new_chat',
      'chat_profiles',
      'user_nav'
    ]);
    for (const name of DEFAULT_MOBILE_HEADER) {
      expect(staysInHeader(name, undefined)).toBe(true);
    }
    expect(staysInHeader('theme', undefined)).toBe(false);
    expect(staysInHeader('share', undefined)).toBe(false);
    expect(staysInHeader('readme', undefined)).toBe(false);
    expect(staysInHeader('api_keys', undefined)).toBe(false);
  });

  it('lets the config replace the list wholesale', () => {
    expect(staysInHeader('theme', ['theme'])).toBe(true);
    expect(staysInHeader('new_chat', ['theme'])).toBe(false);
  });

  it('reads an empty list as a bare header, not as unset', () => {
    expect(staysInHeader('user_nav', [])).toBe(false);
  });
});

describe('Header', () => {
  const link = (name: string, collapse?: boolean) => ({
    name,
    url: `https://example.com/${name}`,
    ...(collapse === undefined ? {} : { collapse_on_mobile: collapse })
  });

  const renderHeader = (ui: Record<string, any> = {}) => {
    mockUseAuth.mockReturnValue({
      data: undefined,
      user: { identifier: 'someone' }
    });
    mockUseConfig.mockReturnValue({
      config: {
        ui,
        chatProfiles: [],
        markdown: '# readme',
        userEnv: ['OPENAI_API_KEY'],
        dataPersistence: true,
        threadSharing: true
      }
    });
    return render(<Header />);
  };

  it('keeps only the named buttons on a narrow screen', () => {
    setWidth(375);
    renderHeader();

    for (const name of DEFAULT_MOBILE_HEADER) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    // The rest live behind the overflow button, whose menu is closed.
    expect(screen.queryByText('theme')).not.toBeInTheDocument();
    expect(screen.queryByText('share')).not.toBeInTheDocument();
    expect(screen.queryByText('readme')).not.toBeInTheDocument();
    expect(screen.queryByText('api_keys')).not.toBeInTheDocument();
    expect(document.querySelector('#header-overflow-button')).not.toBeNull();
  });

  it('places everything and offers no overflow on a wide screen', () => {
    renderHeader();

    for (const name of [
      'new_chat',
      'chat_profiles',
      'share',
      'readme',
      'api_keys',
      'theme',
      'user_nav'
    ]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(document.querySelector('#header-overflow-button')).toBeNull();
  });

  it('follows the config when it names a different set', () => {
    setWidth(375);
    renderHeader({ mobile_header: ['theme'] });

    expect(screen.getByText('theme')).toBeInTheDocument();
    expect(screen.queryByText('new_chat')).not.toBeInTheDocument();
    expect(screen.queryByText('user_nav')).not.toBeInTheDocument();
  });

  it('collapses a header link unless it is pinned', () => {
    setWidth(375);
    renderHeader({
      header_links: [link('Balance', false), link('Issues')]
    });

    expect(screen.getByText('Balance')).toBeInTheDocument();
    expect(screen.queryByText('Issues')).not.toBeInTheDocument();
  });

  it('keeps every link on a wide screen, pinned or not', () => {
    renderHeader({ header_links: [link('Balance', false), link('Issues')] });

    expect(screen.getByText('Balance')).toBeInTheDocument();
    expect(screen.getByText('Issues')).toBeInTheDocument();
  });
});

describe('WaterMark', () => {
  it('stays on one line and keeps the full text in the title', () => {
    const { container } = render(<WaterMark />);

    const watermark = container.querySelector('.watermark') as HTMLElement;
    expect(watermark.getAttribute('title')).toBe(WATERMARK);
    expect(watermark.className).toContain('overflow-hidden');

    // The renderer emits the paragraph as a div, so the clipping rules have
    // to reach `div`, not `p`.
    const markdown = watermark.firstElementChild as HTMLElement;
    expect(markdown.className).toContain('[&_div]:whitespace-nowrap');
    expect(markdown.className).toContain('[&_div]:text-ellipsis');
    expect(markdown.className).toContain('[&_div]:overflow-hidden');
  });
});

describe('AskActionButtons', () => {
  it('stacks the buttons and clips their labels on a narrow screen', () => {
    setWidth(375);
    const label = 'Получить карточки и SKU за 5 credits';
    const { container } = renderAsk([action('confirm', label)], ['confirm']);

    const row = container.firstElementChild as HTMLElement;
    expect(row.className).toContain('flex-col');
    expect(row.className).toContain('w-full');

    const button = screen.getByRole('button');
    expect(button.getAttribute('title')).toBe(label);
    expect(button.className).toContain('w-full');
    expect(button.querySelector('span')?.className).toContain('truncate');
  });

  it('leaves a wide screen wrapping, untitled and unclipped', () => {
    const label = 'Получить карточки и SKU за 5 credits';
    const { container } = renderAsk([action('confirm', label)], ['confirm']);

    const row = container.firstElementChild as HTMLElement;
    expect(row.className).toContain('flex-wrap');
    expect(row.className).not.toContain('flex-col');

    const button = screen.getByRole('button');
    expect(button.getAttribute('title')).toBeNull();
    expect(button.className).toContain('whitespace-normal');
  });

  it('puts the confirming action at the top of the stack', () => {
    setWidth(375);
    // The ask declares the confirming action first; the action list arrives
    // in whatever order the transport filled it.
    renderAsk(
      [action('cancel', 'Отмена'), action('confirm', 'Подтвердить')],
      ['confirm', 'cancel']
    );

    const labels = screen
      .getAllByRole('button')
      .map((button) => button.textContent);
    expect(labels).toEqual(['Подтвердить', 'Отмена']);
  });
});

describe('MessageAvatar', () => {
  it('halves a configured avatar on a narrow screen', () => {
    setWidth(375);
    mockUseConfig.mockReturnValue({
      config: { ui: { avatar_size: 40 }, chatProfiles: [] }
    });

    const { container } = render(<MessageAvatar author="Panda" />);

    expect(container.querySelector('[style*="width: 20px"]')).not.toBeNull();
  });

  it('keeps the configured size on a wide screen', () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { avatar_size: 40 }, chatProfiles: [] }
    });

    const { container } = render(<MessageAvatar author="Panda" />);

    expect(container.querySelector('[style*="width: 40px"]')).not.toBeNull();
  });

  it('leaves the unconfigured default alone', () => {
    setWidth(375);

    const { container } = render(<MessageAvatar author="Panda" />);

    // No `avatar_size`: the 20px class default is small enough already.
    expect(container.querySelector('[style*="width"]')).toBeNull();
  });
});

describe('Step', () => {
  const step = { id: 's1', name: 'search', output: 'done' } as IStep;

  it('takes the full width when the message is stacked', () => {
    setWidth(375);

    const { container } = render(<Step step={step}>content</Step>);

    // `w-0 flex-grow` is a row trick: in the stacked layout it would leave
    // the step at zero width.
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('w-full');
    expect(root.className).not.toContain('w-0');
  });

  it('takes the full width with the details turned off too', () => {
    setWidth(375);

    const { container } = render(
      <MessageContext.Provider
        value={{ ...defaultMessageContext, showStepDetails: false }}
      >
        <Step step={step}>content</Step>
      </MessageContext.Provider>
    );

    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('w-full');
    expect(root.className).not.toContain('w-0');
  });

  it('keeps the shrinkable column next to an avatar', () => {
    const { container } = render(<Step step={step}>content</Step>);

    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('w-0');
  });
});
