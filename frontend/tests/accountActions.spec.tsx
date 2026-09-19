import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createContext } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AccountDialog from '@/components/AccountDialog';

/**
 * What the dialog does with an action.
 *
 * The form draws the buttons `x-actions` declares (agent D's subject) and
 * hands the press up as `(name, path, item)`. From there the dialog owns the
 * whole round trip: one POST, and one of three outcomes — a toast, the page
 * rebuilt, or a hand-off into a fresh chat. The form is stubbed down to a
 * button that presses one action, so what is pinned here is the dialog's
 * half; `accountDialog.spec.tsx` pins the other end, where a real header
 * button reaches this handler.
 */

const mockUseApi = vi.fn();
const mockPost = vi.fn();
const mockMutate = vi.fn();
const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockHandoff = vi.fn();

/** What `SchemaForm` was rendered with, read off the last render. */
let formProps: Record<string, unknown> | null = null;

vi.mock('@chainlit/react-client', () => ({
  ChainlitContext: createContext<any>({}),
  cloneClient: (client: object) =>
    Object.assign(Object.create(Object.getPrototypeOf(client)), client),
  useApi: (path: string) => mockUseApi(path),
  useAuth: () => ({ user: { id: 'u-1', identifier: 'someone@example.com' } }),
  useConfig: () => ({ config: { ui: {}, chatProfiles: [] } })
}));

// The address is what opens the dialog; `?tab=` and where closing leads are
// `accountDialog.spec.tsx`'s subject.
vi.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: '/account', key: 'k1' }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams(), vi.fn()]
}));

// The desktop layout, and one less reason for this spec to answer for the
// package's breakpoint constant: `useIsMobile` reads it off the mocked
// `@chainlit/react-client` above, where it is not.
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }));

// The hook itself is `useSessionHandoff.spec.tsx`'s subject; here it is the
// seam, and the assertion is the payload the page builds out of the outcome.
vi.mock('@/hooks/useSessionHandoff', () => ({
  useSessionHandoff: () => mockHandoff
}));

vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockSuccess(...args),
    error: (...args: unknown[]) => mockError(...args)
  }
}));

// The provider and every piece of the layout it feeds: the dialog imports
// them all, so the factory has to answer for them all. Only the default is
// this spec's seam -- the children are left unrendered, which is what keeps
// the round trip below the subject.
vi.mock('@/components/SchemaForm', () => ({
  LEADING: '$leading',
  useSchemaForm: () => ({ form: { sections: [], tabs: [] }, readonly: false }),
  SchemaSection: () => null,
  SchemaMatches: () => null,
  SchemaSubmit: () => null,
  default: (props: Record<string, unknown>) => {
    formProps = props;
    return (
      <button
        onClick={() =>
          void (
            props.onAction as (
              name: string,
              path: string,
              item: unknown | null
            ) => Promise<void> | void
          )('compare', 'watch.items.3', { title: 'a kettle' })
        }
      >
        stub_action
      </button>
    );
  }
}));

vi.mock('@/components/SchemaForm/Cards', () => ({
  ActionButtons: () => null
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (path: string) => path })
}));

const page = (over: Record<string, unknown> = {}) => ({
  schema: { $ref: '#/$defs/Account', $defs: { Account: {} } },
  values: { notify: true },
  readonly: false,
  message: null,
  ...over
});

/** The POST answers with one outcome; the page decides what it means. */
const answers = (outcome: Record<string, unknown>) =>
  mockPost.mockResolvedValue({ json: async () => ({ outcome }) });

beforeEach(() => {
  vi.clearAllMocks();
  formProps = null;
  mockUseApi.mockReturnValue({
    data: page(),
    error: undefined,
    isLoading: false,
    mutate: mockMutate
  });
  answers({ t: 'toast', message: 'Looking' });
});

const mount = async () => {
  const { ChainlitContext } = await import('@chainlit/react-client');
  return render(
    <ChainlitContext.Provider value={{ post: mockPost }}>
      <AccountDialog />
    </ChainlitContext.Provider>
  );
};

const press = () => fireEvent.click(screen.getByText('stub_action'));

describe('an account action', () => {
  it('posts the pressed action with the element it was pressed on', async () => {
    await mount();
    press();

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        '/project/account/actions/compare',
        { path: 'watch.items.3', item: { title: 'a kettle' } }
      )
    );
  });

  it('says what a toast outcome says', async () => {
    await mount();
    press();

    await waitFor(() => expect(mockSuccess).toHaveBeenCalledWith('Looking'));
    expect(mockMutate).not.toHaveBeenCalled();
    expect(mockHandoff).not.toHaveBeenCalled();
  });

  it('re-renders from a page outcome without asking the server again', async () => {
    const rebuilt = page({ values: { notify: false }, message: 'Compared' });
    answers({ t: 'page', page: rebuilt, message: 'Compared' });

    await mount();
    press();

    // `false`: the hook already answered with the page it rebuilt, so a
    // revalidation would only ask it to repeat itself.
    await waitFor(() =>
      expect(mockMutate).toHaveBeenCalledWith(rebuilt, false)
    );
    expect(mockSuccess).toHaveBeenCalledWith('Compared');
  });

  it('applies a page outcome that carries no message silently', async () => {
    const rebuilt = page({ values: { notify: false } });
    answers({ t: 'page', page: rebuilt, message: null });

    await mount();
    press();

    await waitFor(() =>
      expect(mockMutate).toHaveBeenCalledWith(rebuilt, false)
    );
    expect(mockSuccess).not.toHaveBeenCalled();
  });

  it('takes the hand-off an open_thread outcome describes', async () => {
    answers({
      t: 'open_thread',
      thread_id: 'minted-1',
      chat_profile: 'deep',
      has_transit_message: true
    });

    await mount();
    press();

    // The server's ids, verbatim, in the frame shape the listener's path
    // takes -- nothing about the successor is assembled on this side.
    await waitFor(() =>
      expect(mockHandoff).toHaveBeenCalledWith({
        t: 'session.handoff',
        chatProfile: 'deep',
        nextThreadId: 'minted-1',
        keepTranscript: false,
        hasTransitMessage: true
      })
    );
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('shows the server detail when the action is refused', async () => {
    mockPost.mockRejectedValue(
      Object.assign(new Error('Bad Request'), {
        status: 400,
        detail: 'Expected `str` - at `$.title`'
      })
    );

    await mount();
    press();

    await waitFor(() =>
      expect(mockError).toHaveBeenCalledWith('Expected `str` - at `$.title`')
    );
    expect(mockSuccess).not.toHaveBeenCalled();
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('settles a refusal rather than handing the form a rejection', async () => {
    // A card button has nothing to restore but itself: the promise settling
    // is what re-enables it, and a rejection would only reach the form as an
    // unhandled one.
    mockPost.mockRejectedValue(new Error('offline'));

    await mount();
    const act = formProps?.onAction as (
      name: string,
      path: string,
      item: unknown | null
    ) => Promise<void>;

    await expect(
      act('compare', 'watch.items.0', null)
    ).resolves.toBeUndefined();
  });

  it('posts through a client whose global error toast is off', async () => {
    // One failure, one toast -- the same rule the save follows.
    let seen: unknown;
    const client = {
      post(this: { onError?: unknown }, ...args: unknown[]) {
        seen = this.onError;
        return mockPost(...args);
      },
      onError: () => undefined
    };
    const { ChainlitContext } = await import('@chainlit/react-client');
    render(
      <ChainlitContext.Provider value={client}>
        <AccountDialog />
      </ChainlitContext.Provider>
    );
    press();

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(seen).toBeUndefined();
    expect(client.onError).toBeTypeOf('function');
  });
});
