import { render } from '@testing-library/react';
import { createContext } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Account from '@/pages/Account';

/**
 * The address bar is the tab state.
 *
 * `SchemaForm` is controlled from `?tab=`, so a tab is linkable and survives
 * a reload — and a page opened without the parameter stays clean, with the
 * form falling back to its first tab rather than the page writing one into
 * the URL on mount.
 */

const mockSetSearchParams = vi.fn();
let search = '';

vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(search), mockSetSearchParams]
}));

vi.mock('@chainlit/react-client', () => ({
  ChainlitContext: createContext<any>({}),
  cloneClient: (client: object) => client,
  useApi: () => ({
    data: {
      schema: { $ref: '#/$defs/Account', $defs: { Account: {} } },
      values: {},
      readonly: false,
      message: null
    },
    error: undefined,
    isLoading: false,
    mutate: vi.fn()
  }),
  useAuth: () => ({ user: { id: 'u-1', identifier: 'someone@example.com' } }),
  useConfig: () => ({ config: { ui: {}, chatProfiles: [] } })
}));

vi.mock('@/hooks/useSessionHandoff', () => ({
  useSessionHandoff: () => vi.fn()
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/** What `SchemaForm` was rendered with, read off the last render. */
let formProps: Record<string, unknown> | null = null;

vi.mock('@/components/SchemaForm', () => ({
  default: (props: Record<string, unknown>) => {
    formProps = props;
    return null;
  }
}));

vi.mock('pages/Page', () => ({
  default: ({ children }: { children: JSX.Element }) => <div>{children}</div>
}));

vi.mock('@/components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (path: string) => path })
}));

beforeEach(() => {
  vi.clearAllMocks();
  formProps = null;
  search = '';
});

describe('the account page and ?tab=', () => {
  it('opens the tab the address names', () => {
    search = 'tab=watch';

    render(<Account />);

    expect(formProps?.activeTab).toBe('watch');
  });

  it('says nothing about the tab when the address does not', () => {
    render(<Account />);

    // `undefined`, not the first tab's name: which tab is first is the
    // form's own answer, and the page must not write one into the URL.
    expect(formProps?.activeTab).toBeUndefined();
    expect(mockSetSearchParams).not.toHaveBeenCalled();
  });

  it('writes a chosen tab into the address as a replace', () => {
    render(<Account />);
    (formProps?.onTabChange as (name: string) => void)('calculation');

    expect(mockSetSearchParams).toHaveBeenCalledTimes(1);
    const [updater, options] = mockSetSearchParams.mock.calls[0];
    // Replace, not push: Back leaves the account page rather than walking
    // backwards through the tabs the user looked at.
    expect(options).toEqual({ replace: true });
    expect(updater(new URLSearchParams()).get('tab')).toBe('calculation');
  });

  it('keeps whatever else the address carries', () => {
    render(<Account />);
    (formProps?.onTabChange as (name: string) => void)('watch');

    const [updater] = mockSetSearchParams.mock.calls[0];
    const next = updater(new URLSearchParams('tab=calculation&device=pc'));
    expect(next.get('tab')).toBe('watch');
    expect(next.get('device')).toBe('pc');
  });
});
