import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RecoilRoot } from 'recoil';
import { describe, expect, it, vi } from 'vitest';

import { ThreadList } from '@/components/LeftSidebar/ThreadList';
import { SidebarProvider } from '@/components/ui/sidebar';

/**
 * The heading over a day's worth of threads.
 *
 * Every row in this list is a thread to open; the headings are not, and the
 * only thing that says so is how they are set. Left in the same face and case
 * as the rows they head, they read as one more chat called "Today".
 */

vi.mock('@chainlit/react-client', async () => {
  const { atom } = await import('recoil');
  return {
    // `hooks/use-mobile` reads the breakpoint from the package, so a mock of
    // the whole module has to carry it.
    MOBILE_BREAKPOINT: 768,
    ChainlitContext: (await import('react')).createContext<any>({}),
    ClientError: class extends Error {},
    threadHistoryState: atom({ key: 'testThreadHistory', default: undefined }),
    useChatInteract: () => ({ clear: vi.fn() }),
    useChatMessages: () => ({ threadId: undefined }),
    useChatSession: () => ({ idToResume: undefined }),
    useConfig: () => ({ config: {} })
  };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

vi.mock('@/components/i18n', () => ({
  Translator: ({ path }: { path: string }) => <span>{path}</span>
}));

vi.mock('@/components/share/ShareDialog', () => ({
  default: () => null
}));

vi.mock('@/components/LeftSidebar/ThreadOptions', () => ({
  default: () => null
}));

const history = {
  threads: [],
  timeGroupedThreads: {
    Today: [{ id: 't-1', name: 'a thermos', createdAt: '2026-09-21' }],
    'Previous 7 days': [
      { id: 't-2', name: 'a boot organiser', createdAt: '2026-09-15' }
    ]
  }
} as any;

const mount = () =>
  render(
    <RecoilRoot>
      <MemoryRouter>
        <SidebarProvider>
          <ThreadList
            threadHistory={history}
            isFetching={false}
            isLoadingMore={false}
          />
        </SidebarProvider>
      </MemoryRouter>
    </RecoilRoot>
  );

describe('the time-group heading', () => {
  it('is set apart from the threads it heads', () => {
    const { container } = mount();

    const labels = Array.from(
      container.querySelectorAll('[data-sidebar="group-label"]')
    );
    expect(labels).toHaveLength(2);
    for (const label of labels) {
      expect(label.className).toContain('uppercase');
      expect(label.className).toContain('font-mono');
      expect(label.className).toContain('tracking-wider');
    }
  });
});
