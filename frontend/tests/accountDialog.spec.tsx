import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createContext } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AccountDialog from '@/components/AccountDialog';

/**
 * The account is a modal over the chat, open exactly when the address says
 * `/account`.
 *
 * What the dialog owns is everything that is not a field: whether it is on
 * screen at all, where closing it leads, which of the four fetch states the
 * page is in, the section menu and its `?tab=`, the search box, and the round
 * trip a save makes. The form itself is a provider (agent D's subject), so
 * every piece of it is stubbed here — `SchemaSection`, `SchemaMatches`,
 * `SchemaSubmit` and `useSchemaForm` — and the stubs are wired to each other
 * so a press on a header action really travels through the dialog's
 * `onAction` to the POST.
 */

const mockUseApi = vi.fn();
const mockUseAuth = vi.fn();
const mockUseConfig = vi.fn();
const mockPut = vi.fn();
const mockPost = vi.fn();
const mockMutate = vi.fn();
const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockNavigate = vi.fn();
const mockSetSearchParams = vi.fn();

/** What `SchemaForm` was rendered with, read off the last render. */
let formProps: Record<string, any> | null = null;
/** What `useSchemaForm` hands the layout back. */
let resolved: any = { sections: [], tabs: [] };
let location: { pathname: string; key: string } = {
  pathname: '/account',
  key: 'k1'
};
let search = '';
let mobile = false;

vi.mock('@chainlit/react-client', () => ({
  ChainlitContext: createContext<any>({}),
  // The package's own shallow copy, as the dialog uses it: prototype kept,
  // fields assigned. The spec that reads `this.onError` off `put` relies on
  // the copy having the method through its prototype.
  cloneClient: (client: object) =>
    Object.assign(Object.create(Object.getPrototypeOf(client)), client),
  useApi: (path: string) => mockUseApi(path),
  useAuth: () => mockUseAuth(),
  useConfig: () => mockUseConfig()
}));

vi.mock('react-router-dom', () => ({
  useLocation: () => location,
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(search), mockSetSearchParams]
}));

vi.mock('@/hooks/useSessionHandoff', () => ({
  useSessionHandoff: () => vi.fn()
}));

// The phone layout is a different tree, not a media query: driven from the
// hook so a spec can ask for either one.
vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => mobile
}));

vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockSuccess(...args),
    error: (...args: unknown[]) => mockError(...args)
  }
}));

vi.mock('@/components/SchemaForm', () => ({
  LEADING: '$leading',
  default: (props: Record<string, any>) => {
    formProps = props;
    return <form className={props.className}>{props.children}</form>;
  },
  // The provider, answered from the props the dialog just passed: that is
  // what the real one does, and it keeps the two halves of the stub honest.
  useSchemaForm: () => ({
    form: resolved,
    readonly: !!formProps?.readonly,
    onAction: formProps?.onAction,
    isSubmitting: false
  }),
  SchemaSection: ({ name }: { name: string }) => (
    <div data-testid="section">{name}</div>
  ),
  SchemaMatches: ({ query }: { query: string }) => (
    <div data-testid="matches">{query}</div>
  ),
  SchemaSubmit: () => (
    <button
      type="button"
      onClick={() =>
        // The real form owns the rejection (it is what re-enables the
        // button); swallowed here so a failing-save case does not surface as
        // an unhandled rejection in the run.
        void Promise.resolve(formProps?.onSubmit({ a: 1 })).catch(
          () => undefined
        )
      }
    >
      stub_submit
    </button>
  )
}));

vi.mock('@/components/SchemaForm/Cards', () => ({
  ActionButtons: ({ actions, path, item, onAction }: Record<string, any>) =>
    actions?.length ? (
      <>
        {actions.map((action: any) => (
          <button
            key={action.name}
            type="button"
            onClick={() => void onAction?.(action.name, path, item)}
          >
            {action.label}
          </button>
        ))}
      </>
    ) : null
}));

vi.mock('@/components/Icon', () => ({
  default: ({ name }: { name: string }) => (
    <span data-testid="icon" data-name={name} />
  )
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
  location = { pathname: '/account', key: 'k1' };
  search = '';
  mobile = false;
  resolved = {
    sections: [{ name: 'notify', title: 'Notify' }],
    tabs: [
      {
        name: 'calculation',
        title: 'Расчёт',
        description: 'Параметры расчёта',
        icon: 'calculator',
        fields: []
      },
      {
        name: 'watch',
        title: 'Мои товары',
        fields: [],
        actions: [{ name: 'compare', label: 'Где дешевле', icon: 'search' }]
      }
    ]
  };
  mockUseAuth.mockReturnValue({ user: someone });
  mockUseConfig.mockReturnValue({ config: { ui: {}, chatProfiles: [] } });
  mockPut.mockResolvedValue({ json: async () => page({ message: 'Stored' }) });
  mockPost.mockResolvedValue({
    json: async () => ({ outcome: { t: 'toast', message: 'Looking' } })
  });
  respond({ data: page() });
});

/** The context value is the API client; the dialog reads `put` off it. */
const mount = async (client: object = { put: mockPut, post: mockPost }) => {
  const { ChainlitContext } = await import('@chainlit/react-client');
  return render(
    <ChainlitContext.Provider value={client}>
      <AccountDialog />
    </ChainlitContext.Provider>
  );
};

/** The menu rows, in the order they are drawn. */
const menu = () =>
  screen
    .getAllByRole('button')
    .filter((button) => button.querySelector('[data-testid="icon"]'));

describe('the account dialog', () => {
  it('stays shut, and asks for nothing, on every other address', async () => {
    location = { pathname: '/', key: 'k1' };

    await mount();

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // The fetch is the point: `GET /project/account` is how the application
    // marks things seen, so running it on the chat route would blank the
    // badge for a user who never opened the account.
    expect(mockUseApi).not.toHaveBeenCalled();
  });

  it('opens on /account and asks the engine for the page', async () => {
    await mount();

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(mockUseApi).toHaveBeenCalledWith('/project/account');
  });

  it('goes back when the dialog was reached from inside the app', async () => {
    await mount();
    fireEvent.click(screen.getByText('account.close'));

    expect(mockNavigate).toHaveBeenCalledWith(-1);
  });

  it('goes home when /account was the first entry in the tab', async () => {
    // react-router's own marker for an entry it did not create: there is
    // nothing behind this one, and a `-1` would leave the app.
    location = { pathname: '/account', key: 'default' };

    await mount();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true })
    );
  });

  it('shows placeholders while the schema is in flight', async () => {
    respond({ isLoading: true });

    await mount();

    expect(document.querySelectorAll('.animate-pulse').length).toBeGreaterThan(
      0
    );
    expect(screen.queryByTestId('section')).not.toBeInTheDocument();
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

  it('names the signed-in user beside the menu', async () => {
    await mount();

    expect(screen.getByText('Someone')).toBeInTheDocument();
    expect(screen.getByText('someone@example.com')).toBeInTheDocument();
    // The heading is the dialog's accessible name; the visible headings are
    // the sections'.
    expect(screen.getByText('account.title')).toBeInTheDocument();
  });

  it('prefers the configured heading over the translation', async () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { account: { enabled: true, title: 'Кабинет' } } }
    });

    await mount();

    expect(screen.getByText('Кабинет')).toBeInTheDocument();
    expect(screen.queryByText('account.title')).not.toBeInTheDocument();
  });

  it('lists General first, then the sections with their icons', async () => {
    await mount();

    expect(menu().map((row) => row.textContent)).toEqual([
      'account.general',
      'Расчёт',
      'Мои товары'
    ]);
    expect(
      menu().map((row) =>
        row.querySelector('[data-testid="icon"]')?.getAttribute('data-name')
      )
      // The section's own `x-icon`, and `settings-2` for a section that
      // declared none -- a row with no icon at all would leave the list
      // ragged.
    ).toEqual(['settings-2', 'calculator', 'settings-2']);
  });

  it('opens the section the address names', async () => {
    search = 'tab=watch';

    await mount();

    expect(screen.getByTestId('section')).toHaveTextContent('watch');
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      'Мои товары'
    );
  });

  it('opens the first section when ?tab= names none', async () => {
    search = 'tab=gone';

    await mount();

    // The leading scalars: `?tab=` outlives a rename, and the dialog opens
    // on something rather than on nothing.
    expect(screen.getByTestId('section')).toHaveTextContent('$leading');
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      'account.general'
    );
  });

  it('writes a chosen section into the address as a replace', async () => {
    await mount();
    fireEvent.click(screen.getByText('Мои товары'));

    expect(mockSetSearchParams).toHaveBeenCalledTimes(1);
    const [updater, options] = mockSetSearchParams.mock.calls[0];
    // Replace, not push: Back leaves the account rather than walking
    // backwards through the sections the user looked at.
    expect(options).toEqual({ replace: true });
    const next = updater(new URLSearchParams('tab=calculation&device=pc'));
    expect(next.get('tab')).toBe('watch');
    expect(next.get('device')).toBe('pc');
  });

  it('replaces the section body with the matches while searching', async () => {
    await mount();
    fireEvent.change(screen.getByLabelText('account.search.placeholder'), {
      target: { value: '  маржа  ' }
    });

    // Trimmed, exactly: the query is a needle, and a stray space would match
    // nothing.
    expect(screen.getByTestId('matches').textContent).toBe('маржа');
    expect(screen.queryByTestId('section')).not.toBeInTheDocument();
    // The menu stays: the matches are a view over all of it.
    expect(menu()).toHaveLength(3);
  });

  it('clears the query when a section is chosen', async () => {
    await mount();
    const box = screen.getByLabelText('account.search.placeholder');
    fireEvent.change(box, { target: { value: 'маржа' } });
    fireEvent.click(screen.getByText('Мои товары'));

    expect(screen.queryByTestId('matches')).not.toBeInTheDocument();
    expect(screen.getByTestId('section')).toBeInTheDocument();
    expect(box).toHaveValue('');
  });

  it('refuses Enter in the search box', async () => {
    await mount();
    const box = screen.getByLabelText('account.search.placeholder');

    // The box is inside the `<form>` the account is saved with, where Enter
    // is a submit: finishing a query would save the account.
    expect(fireEvent.keyDown(box, { key: 'Enter' })).toBe(false);
    expect(fireEvent.keyDown(box, { key: 'a' })).toBe(true);
  });

  it('posts a header action against the section it belongs to', async () => {
    search = 'tab=watch';

    await mount();
    fireEvent.click(screen.getByText('Где дешевле'));

    // The section name as the path and no item: a tab action addresses the
    // Struct field, not an element of it.
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        '/project/account/actions/compare',
        { path: 'watch', item: null }
      )
    );
    expect(mockSuccess).toHaveBeenCalledWith('Looking');
  });

  it('puts the section description under its title', async () => {
    search = 'tab=calculation';

    await mount();

    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      'Расчёт'
    );
    expect(screen.getByText('Параметры расчёта')).toBeInTheDocument();
    // Another section's buttons are that section's.
    expect(screen.queryByText('Где дешевле')).not.toBeInTheDocument();
  });

  it('warns that a read-only account cannot be saved', async () => {
    respond({ data: page({ readonly: true }) });

    await mount();

    expect(screen.getByText('account.readonly')).toBeInTheDocument();
    // Still rendered, still read-only: the values are worth seeing even
    // where the app offers no way to change them.
    expect(formProps?.readonly).toBe(true);
    expect(screen.queryByText('stub_submit')).not.toBeInTheDocument();
  });

  it('hands the schema and the values to the form untouched', async () => {
    await mount();

    expect(formProps?.schema).toBe(SCHEMA);
    expect(formProps?.values).toEqual({ notify: true });
    expect(formProps?.readonly).toBe(false);
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
    const boom = Object.assign(new Error('Bad Request'), {
      status: 400,
      detail: 'Expected `float` <= 100.0 - at `$.calculation.margin`'
    });
    mockPut.mockRejectedValue(boom);

    await mount();
    fireEvent.click(screen.getByText('stub_submit'));

    await waitFor(() =>
      expect(mockError).toHaveBeenCalledWith(
        'Expected `float` <= 100.0 - at `$.calculation.margin`'
      )
    );
    expect(mockSuccess).not.toHaveBeenCalled();
    expect(mockMutate).not.toHaveBeenCalled();
    // Rethrown: the form is what disabled itself for the round trip, and
    // only the rejection puts the Save button back.
    await expect(formProps?.onSubmit({ a: 1 })).rejects.toBe(boom);
  });

  it('saves through a client whose global error toast is off', async () => {
    // One failure, one toast. The context client toasts every `ClientError`
    // it raises, so a save sent through it would say it twice -- once as
    // `Bad Request: <detail>` and once as the detail alone.
    let seen: unknown;
    // A method, not an arrow: `this` has to be the object the dialog
    // actually called `put` on, which is the point of the assertion.
    const client = {
      put(this: { onError?: unknown }, ...args: unknown[]) {
        seen = this.onError;
        return mockPut(...args);
      },
      onError: () => undefined
    };

    await mount(client);
    fireEvent.click(screen.getByText('stub_submit'));

    await waitFor(() => expect(mockPut).toHaveBeenCalled());
    expect(seen).toBeUndefined();
    // The context client itself is untouched -- other callers still get the
    // global toast.
    expect(client.onError).toBeTypeOf('function');
  });

  it('turns the menu into a strip of chips on a phone', async () => {
    mobile = true;

    await mount();

    // The same rows, no column: a phone has no room for a window over a
    // window, so the dialog takes the screen and the menu goes on top.
    expect(menu().map((row) => row.textContent)).toEqual([
      'account.general',
      'Расчёт',
      'Мои товары'
    ]);
    expect(document.querySelector('.w-60')).toBeNull();
    expect(document.querySelector('.overflow-x-auto')).not.toBeNull();
    // The user block is the left column's; the strip is the search box and
    // the chips.
    expect(screen.queryByText('someone@example.com')).not.toBeInTheDocument();
  });
});
