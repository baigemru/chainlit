import { renderHook } from '@testing-library/react';
import { ReactNode } from 'react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { currentThreadIdState, sessionIdState } from '@chainlit/react-client';

import { useParentThreadId } from '@/hooks/useParentThread';

import { parentThreadEntryState } from '@/state/chat';

/**
 * `ui.show_parent_thread_button` has exactly one reader. Both display sites —
 * the return button and the composer's test for an empty left slot — go
 * through this hook, so the flag is tested here and nowhere else; a second
 * copy of the condition is precisely what folding it in here prevents.
 */

const mockUseConfig = vi.fn();

vi.mock('@chainlit/react-client', async () => {
  // Real atoms, not stubs: the scoping rules below are the hook's own, and a
  // stub that always answered would test the test.
  const { atom } = await import('recoil');
  return {
    sessionIdState: atom<string | undefined>({
      key: 'test/SessionId',
      default: undefined
    }),
    currentThreadIdState: atom<string | undefined>({
      key: 'test/CurrentThreadId',
      default: undefined
    }),
    useConfig: () => mockUseConfig()
  };
});

const wrapper =
  (sessionId: string | undefined) =>
  ({ children }: { children: ReactNode }) => (
    <RecoilRoot
      initializeState={({ set }) => {
        set(parentThreadEntryState, {
          parentThreadId: 'parent-thread',
          forSessionId: 'session-1'
        });
        set(sessionIdState, sessionId);
        set(currentThreadIdState, undefined);
      }}
    >
      {children}
    </RecoilRoot>
  );

const parentThreadId = (sessionId: string | undefined = 'session-1') =>
  renderHook(() => useParentThreadId(), { wrapper: wrapper(sessionId) }).result
    .current;

describe('useParentThreadId, behind ui.show_parent_thread_button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the parent to itself in a deployment that never asked', () => {
    mockUseConfig.mockReturnValue({ config: { ui: {} } });

    expect(parentThreadId()).toBeUndefined();
  });

  it('keeps it to itself when the flag is off', () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { show_parent_thread_button: false } }
    });

    expect(parentThreadId()).toBeUndefined();
  });

  it('answers with the parent once the deployment asks for the button', () => {
    mockUseConfig.mockReturnValue({
      config: { ui: { show_parent_thread_button: true } }
    });

    expect(parentThreadId()).toBe('parent-thread');
  });

  it('still refuses a parent learned for another session', () => {
    // The flag is a display switch laid over the scoping, not instead of it.
    mockUseConfig.mockReturnValue({
      config: { ui: { show_parent_thread_button: true } }
    });

    expect(parentThreadId('session-2')).toBeUndefined();
  });
});
