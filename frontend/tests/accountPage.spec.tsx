import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createContext } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Account from '@/pages/Account';

/**
 * The account page owns three things and nothing else: which of the four
 * states the fetch is in, what it hands `SchemaForm`, and what a save does
 * with the server's answer. The form itself is agent B's subject, so it is a
 * stub here — one button that submits a known object, which is enough to pin
 * the round trip.
 */

const mockUseApi = vi.fn();
const mockUseAuth = vi.fn();
const mockUseConfig = vi.fn();
const mockPut = vi.fn();
const mockMutate = vi.fn();
const mockSuccess = vi.fn();
const mockError = vi.fn();

/** What `SchemaForm` was rendered with, read off the last render. */
let formProps: Record<string, unknown> | null = null;

vi.mock('@chainlit/react-client', () => ({
  ChainlitContext: createContext<any>({}),
  // The package's own shallow copy, as the page uses it: prototype kept,
  // fields assigned. The spec that reads `this.onError` off `put` relies on
  // the copy having the method through its prototype.
  cloneClient: (client: object) =>
    Object.assign(Object.create(Object.getPrototypeOf(client)), client),
  useApi: (path: string) => mockUseApi(path),
  useAuth: () => mockUseAuth(),
  useConfig: () => mockUseConfig()
}));

// The page reads `?tab=` and can take a hand-off now; neither is this
// spec's subject (`accountTab` and `accountActions` own them), so both are
// answered with the quietest thing that keeps the page mounting.
vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(), vi.fn()]
}));

vi.mock('@/hooks/useSessionHandoff', () => ({
  useSessionHandoff: () => vi.fn()
}));

vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockSuccess(...args),
    error: (...args: unknown[]) => mockError(...args)
  }
}));

vi.mock('@/components/SchemaForm', () => ({
  default: (props: Record<string, unknown>) => {
    formProps = props;
    return (
      <button
        onClick={() =>
          // The real form owns the rejection (it is what re-enables the
          // button); swallowed here so a failing-save case does not surface
          // as an unhandled rejection in the run.
          void Promise.resolve(
            (props.onSubmit as (v: unknown) => Promise<void> | void)({ a: 1 })
          ).catch(() => undefined)
        }
      >
        stub_submit
      </button>
    );
  }
}));

// `Page` is the chat shell -- sidebars, the header, the resume listener. The
// page under test is what it wraps.
vi.mock('pages/Page', () => ({
  default: ({ children }: { children: JSX.Element }) => <div>{children}</div>
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (path: string) => path })
}));

const someone = {
  id: 'u-1',
  identifier: 'someone@example.com',
  display_name: 'Someone',
  metadata: { image: 'https://example.com/a.png' }
};

const SCHEMA = { $ref: '#/$defs/Account', $defs: { Account: {} } };

const page = (over: Record<string, unknown> = {}) => ({
  schema: SCHEMA,
  values: { notify: true },
  readonly: false,
  message: null,
  ...over
});

/** The four shapes `useApi` can be in, stated once. */
const respond = (over: Record<string, unknown>) =>
  mockUseApi.mockReturnValue({
    data: undefined,
    error: undefined,
    isLoading: false,
    mutate: mockMutate,
    ...over
  });

beforeEach(() => {
  vi.clearAllMocks();
  formProps = null;
  mockUseAuth.mockReturnValue({ user: someone });
  mockUseConfig.mockReturnValue({ config: { ui: {}, chatProfiles: [] } });
  mockPut.mockResolvedValue({ json: async () => page({ message: 'Stored' }) });
  respond({ data: page() });
});

/** The context value is the API client; the page reads `put` off it. */
const mount = async () => {
  const { ChainlitContext } = await import('@chainlit/react-client');
  return render(
    <ChainlitContext.Provider value={{ put: mockPut }}>
      <Account />
    </ChainlitContext.Provider>
  );
};

describe('the account page', () => {
  it('asks the engine for the page it is showing', async () => {
    await mount();

    expect(mockUseApi).toHaveBeenCalledWith('/project/account');
  });

  it('shows placeholders while the schema is in flight', async () => {
    respond({ isLoading: true });

    const { container } = await mount();

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(
      0
    );
    expect(screen.queryByText('stub_submit')).not.toBeInTheDocument();
  });

  it('says the application declared no account page on a 404', async () => {
    respond({ error: Object.assign(new Error('Not Found'), { status: 404 }) });

    await mount();

    expect(screen.getByText('account.notConfigured')).toBeInTheDocument();
    // Not the generic failure: a 404 here is a configuration fact, not a
    // breakage, and it must not read as one.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('reports any other failure with the server detail', async () => {
    respond({
      error: Object.assign(new Error('Internal Server Error'), {
        status: 500,
        detail: 'the data layer is down'
      })
    });

    await mount();

    expect(screen.getByText('the data layer is down')).toBeInTheDocument();
    expect(screen.queryByText('account.notConfigured')).not.toBeInTheDocument();
  });

  it('names the signed-in user above the form', async () => {
    await mount();

    expect(screen.getByText('Someone')).toBeInTheDocument();
    expect(screen.getByText('someone@example.com')).toBeInTheDocument();
    expect(screen.getByText('account.title')).toBeInTheDocument();
    expect(screen.getByText('account.description')).toBeInTheDocument();
  });

  it('prefers the configured heading over the translation', async () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { account: { enabled: true, title: 'Кабинет' } } }
    });

    await mount();

    expect(screen.getByText('Кабинет')).toBeInTheDocument();
    expect(screen.queryByText('account.title')).not.toBeInTheDocument();
  });

  it('hands the schema and the values to the form untouched', async () => {
    await mount();

    expect(formProps?.schema).toBe(SCHEMA);
    expect(formProps?.values).toEqual({ notify: true });
    expect(formProps?.readonly).toBe(false);
  });

  it('warns that a read-only page cannot be saved', async () => {
    respond({ data: page({ readonly: true }) });

    await mount();

    expect(screen.getByText('account.readonly')).toBeInTheDocument();
    // Still rendered, still read-only: the values are worth seeing even
    // where the app offers no way to change them.
    expect(formProps?.readonly).toBe(true);
  });

  it('puts the values back and shows what the server stored', async () => {
    await mount();
    fireEvent.click(screen.getByText('stub_submit'));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('/project/account', { a: 1 })
    );
    // The response body, not the submitted values: the engine answers with
    // what it stored, which a lenient decode may have trimmed.
    expect(mockMutate).toHaveBeenCalledWith(page({ message: 'Stored' }), false);
    expect(mockSuccess).toHaveBeenCalledWith('Stored');
    expect(mockError).not.toHaveBeenCalled();
  });

  it('falls back to the saved translation when the app said nothing', async () => {
    mockPut.mockResolvedValue({ json: async () => page() });

    await mount();
    fireEvent.click(screen.getByText('stub_submit'));

    await waitFor(() =>
      expect(mockSuccess).toHaveBeenCalledWith('account.saved')
    );
  });

  it('shows the rejected save detail, the only field addressing there is', async () => {
    mockPut.mockRejectedValue(
      Object.assign(new Error('Bad Request'), {
        status: 400,
        detail: 'Expected `float` <= 100.0 - at `$.calculation.margin`'
      })
    );

    await mount();
    fireEvent.click(screen.getByText('stub_submit'));

    await waitFor(() =>
      expect(mockError).toHaveBeenCalledWith(
        'Expected `float` <= 100.0 - at `$.calculation.margin`'
      )
    );
    expect(mockSuccess).not.toHaveBeenCalled();
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('lets the rejection reach the form so the button comes back', async () => {
    const boom = Object.assign(new Error('Bad Request'), { status: 400 });
    mockPut.mockRejectedValue(boom);

    await mount();
    const submit = formProps?.onSubmit as (v: unknown) => Promise<void> | void;

    await expect(submit({ a: 1 })).rejects.toBe(boom);
  });

  it('saves through a client whose global error toast is off', async () => {
    // One failure, one toast. The context client toasts every `ClientError`
    // it raises, so a save sent through it would say it twice -- once as
    // `Bad Request: <detail>` and once as the detail alone.
    let seen: unknown;
    // A method, not an arrow: `this` has to be the object the page actually
    // called `put` on, which is the point of the assertion.
    const client = {
      put(this: { onError?: unknown }, ...args: unknown[]) {
        seen = this.onError;
        return mockPut(...args);
      },
      onError: () => undefined
    };
    const { ChainlitContext } = await import('@chainlit/react-client');
    render(
      <ChainlitContext.Provider value={client}>
        <Account />
      </ChainlitContext.Provider>
    );
    fireEvent.click(screen.getByText('stub_submit'));

    await waitFor(() => expect(mockPut).toHaveBeenCalled());
    expect(seen).toBeUndefined();
    // The context client itself is untouched -- other callers still get the
    // global toast.
    expect(client.onError).toBeTypeOf('function');
  });
});
