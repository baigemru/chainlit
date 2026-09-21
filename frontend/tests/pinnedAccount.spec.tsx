import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { IJsonSchema } from '@chainlit/react-client';

import PinnedAccount from '@/components/LeftSidebar/PinnedAccount';
import { SidebarProvider } from '@/components/ui/sidebar';

/**
 * The pinned account sections, as rows above the chat history.
 *
 * What the block owns is a claim about the schema and the address: which
 * sections asked to be here, in which order, where each row leads, and which
 * one the account dialog currently has open. The router is real — `Link`,
 * `useLocation` and `useSearchParams` are the subject, not a dependency — and
 * so is `resolveForm`; only the config, the user and the icon font are stood
 * in for.
 */

const mockUseConfig = vi.fn();
const mockUseAuth = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useConfig: () => mockUseConfig(),
  useAuth: () => mockUseAuth()
}));

// Desktop width: on a phone `Sidebar` is a Radix sheet, and this block is the
// same rows either way.
vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => false
}));

// The name asked for is the assertion; a real lucide `<svg>` would only let
// the test check that *an* icon rendered.
vi.mock('@/components/Icon', () => ({
  default: ({ name }: { name: string }) => (
    <span data-testid="icon" data-name={name} />
  )
}));

const section = (title: string, extra: Record<string, unknown> = {}) => ({
  title,
  ...extra,
  $ref: '#/$defs/Leaf'
});

/** Two pinned sections around one that never asked, plus a scalar field. */
const SCHEMA = {
  $ref: '#/$defs/Account',
  $defs: {
    Account: {
      title: 'Account',
      type: 'object',
      properties: {
        items: section('Мои товары', { 'x-pinned': true, 'x-icon': 'package' }),
        reports: section('Отчёты'),
        feed: section('Лента', { 'x-pinned': true }),
        notify: { title: 'Уведомления', type: 'boolean', default: true }
      },
      required: []
    },
    Leaf: {
      title: 'Leaf',
      type: 'object',
      properties: { note: { title: 'Заметка', type: 'string', default: '' } },
      required: []
    }
  }
} as IJsonSchema;

const someone = { id: 'u-1', identifier: 'someone@example.com' };

const configure = (account?: Record<string, unknown>) =>
  mockUseConfig.mockReturnValue({ config: { ui: { account }, features: {} } });

const mount = (at = '/') =>
  render(
    <MemoryRouter initialEntries={[at]}>
      <SidebarProvider>
        <PinnedAccount />
      </SidebarProvider>
    </MemoryRouter>
  );

const rows = () => screen.queryAllByRole('link');

/**
 * `SidebarProvider`'s own wrapper is always in the container, so "nothing"
 * is the wrapper standing empty — which is also the claim that matters: no
 * group, and so none of the group's padding between the header and the
 * history.
 */
const nothing = (container: HTMLElement) =>
  expect(container.firstElementChild).toBeEmptyDOMElement();

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: someone });
  configure({ enabled: true, title: '', schema: SCHEMA });
});

describe('the pinned account sections', () => {
  it('draws one row per pinned section, in the schema order', () => {
    const { container } = mount();

    // `reports` declared no `x-pinned`, `notify` is a scalar and not a
    // section at all.
    expect(rows().map((row) => row.textContent)).toEqual([
      'Мои товары',
      'Лента'
    ]);
    expect(container.querySelector('[data-sidebar="group"]')).not.toBeNull();
  });

  it('closes the block with a rule against the history below it', () => {
    const { container } = mount();

    expect(
      container.querySelector('[data-sidebar="separator"]')
    ).not.toBeNull();
  });

  it('draws no rule when it drew no rows', () => {
    // A line under nothing: the sidebar cannot tell, which is why the
    // separator is this component's and not the sidebar's.
    mockUseAuth.mockReturnValue({ user: null });
    const { container } = mount();

    expect(container.querySelector('[data-sidebar="separator"]')).toBeNull();
  });

  it('sends each row to its section of the account dialog', () => {
    mount();

    expect(rows().map((row) => row.getAttribute('href'))).toEqual([
      '/account?tab=items',
      '/account?tab=feed'
    ]);
  });

  it('takes the icon from x-icon and falls back for a section without one', () => {
    mount();

    expect(
      screen
        .getAllByTestId('icon')
        .map((icon) => icon.getAttribute('data-name'))
    ).toEqual(['package', 'settings-2']);
  });

  it('lights the row whose section the dialog has open', () => {
    mount('/account?tab=feed');

    const buttons = screen.getAllByRole('link');
    expect(buttons.map((row) => row.getAttribute('data-active'))).toEqual([
      'false',
      'true'
    ]);
  });

  it('lights nothing when ?tab= rides along on another address', () => {
    // The account dialog is mounted on `/account` and nowhere else, so a
    // `?tab=` left over in the address of a chat names no open section.
    mount('/thread/a?tab=feed');

    expect(
      screen.getAllByRole('link').map((row) => row.getAttribute('data-active'))
    ).toEqual(['false', 'false']);
  });

  it('lights nothing when the open section is not a pinned one', () => {
    mount('/account?tab=reports');

    expect(
      screen.getAllByRole('link').map((row) => row.getAttribute('data-active'))
    ).toEqual(['false', 'false']);
  });

  it('renders nothing when the application declared no account page', () => {
    configure(undefined);
    const { container } = mount();

    nothing(container);
  });

  it('renders nothing when the account page is declared but switched off', () => {
    configure({ enabled: false, title: '', schema: SCHEMA });
    const { container } = mount();

    nothing(container);
  });

  it('renders nothing before the schema has arrived', () => {
    // The config of a client running against a server that does not attach
    // the schema yet — and the first paint of one that does.
    configure({ enabled: true, title: '' });
    const { container } = mount();

    nothing(container);
  });

  it('renders nothing for nobody', () => {
    // The sections are the signed-in user's own; the login screen must not
    // offer a way into them.
    mockUseAuth.mockReturnValue({ user: null });
    const { container } = mount();

    nothing(container);
  });

  it('renders nothing when no section asked to be pinned', () => {
    configure({
      enabled: true,
      title: '',
      schema: {
        ...SCHEMA,
        $defs: {
          ...SCHEMA.$defs,
          Account: {
            ...SCHEMA.$defs!.Account,
            properties: { reports: section('Отчёты') }
          }
        }
      }
    });
    const { container } = mount();

    // Not an empty group with its padding: the header sits on the history
    // exactly as it did before this block existed.
    nothing(container);
  });
});
