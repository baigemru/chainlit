import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useHasLeftSidebar } from '@/hooks/useHasLeftSidebar';

/**
 * The one gate for mounting the sidebar, showing the header's trigger and
 * turning the composer's chevron into a control. It is a conjunction of three
 * independent facts, so each one is failed on its own here: a spec that moved
 * two of them together would let either be deleted and stay green — and
 * deleting the `"hidden"` clause in particular buys a deployment a chevron
 * that toggles a sidebar no page ever mounted.
 */

const mockUseAuth = vi.fn();
const mockUseConfig = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useAuth: () => mockUseAuth(),
  useConfig: () => mockUseConfig()
}));

const hasSidebar = (
  requireLogin: boolean,
  dataPersistence: boolean,
  defaultSidebarState?: string
) => {
  mockUseAuth.mockReturnValue({ data: requireLogin ? { requireLogin } : {} });
  mockUseConfig.mockReturnValue({
    config: {
      dataPersistence,
      ui: { default_sidebar_state: defaultSidebarState }
    }
  });

  return renderHook(() => useHasLeftSidebar()).result.current;
};

describe('useHasLeftSidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('is false without a login: the history belongs to a user', () => {
    expect(hasSidebar(false, true, 'open')).toBe(false);
  });

  it('is false without persistence: there is nothing to list', () => {
    expect(hasSidebar(true, false, 'open')).toBe(false);
  });

  it('is false when the deployment hides the sidebar', () => {
    // "hidden" is the app saying it wants neither the sidebar nor anything
    // that opens it — a trigger for something never mounted is a dead control.
    expect(hasSidebar(true, true, 'hidden')).toBe(false);
  });

  it('is true once all three hold', () => {
    expect(hasSidebar(true, true, 'open')).toBe(true);
    expect(hasSidebar(true, true, 'closed')).toBe(true);
    // An app that never named a state gets the built-in one, which is not
    // "hidden".
    expect(hasSidebar(true, true, undefined)).toBe(true);
  });

  it('answers false rather than throwing before config and auth land', () => {
    mockUseAuth.mockReturnValue({});
    mockUseConfig.mockReturnValue({});

    expect(renderHook(() => useHasLeftSidebar()).result.current).toBe(false);
  });
});
