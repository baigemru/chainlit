import { act, render } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LeftSidebar from '@/components/LeftSidebar';
import { SidebarProvider, useSidebar } from '@/components/ui/sidebar';

/**
 * On a phone the sidebar is a modal sheet, and every choice made inside it
 * navigates: a thread, a search hit, a delete that walks away from the thread
 * it removed. Each of those used to leave the sheet standing over the chat it
 * had just opened, and `/thread/a` → `/thread/b` still did even where a route
 * remount happened to close it. One effect on the address answers for all of
 * them, which is why the call sites are not the subject here — the address is.
 */

// Desktop width, so the sidebar is the plain div and no Radix portal has to be
// driven; `openMobile` is provider state either way, and it is the fact under
// test.
vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => false
}));

// The sidebar's contents fetch threads and open dialogs; what it does with the
// address is the subject.
vi.mock('@/components/LeftSidebar/ThreadHistory', () => ({
  ThreadHistory: () => null
}));
vi.mock('@/components/LeftSidebar/PinnedAccount', () => ({
  default: () => null
}));
vi.mock('@/components/LeftSidebar/Search', () => ({ default: () => null }));
vi.mock('@/components/header/NewChat', () => ({ default: () => null }));
vi.mock('@/components/header/SidebarTrigger', () => ({ default: () => null }));

let probe: {
  openMobile: boolean;
  setOpenMobile: (open: boolean) => void;
  navigate: (to: string) => void;
};

const Probe = () => {
  const { openMobile, setOpenMobile } = useSidebar();
  const navigate = useNavigate();
  probe = { openMobile, setOpenMobile, navigate };
  return null;
};

const tree = (at: string) => (
  <RecoilRoot>
    <MemoryRouter initialEntries={[at]}>
      <SidebarProvider>
        <Probe />
        <LeftSidebar />
      </SidebarProvider>
    </MemoryRouter>
  </RecoilRoot>
);

const mount = (at: string) => render(tree(at));

describe('LeftSidebar, on a phone', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('closes the sheet when something inside it moves the address', () => {
    mount('/thread/a');

    act(() => probe.setOpenMobile(true));
    expect(probe.openMobile).toBe(true);

    act(() => probe.navigate('/thread/b'));

    expect(probe.openMobile).toBe(false);
  });

  it('closes it for a tap that lands on the address already shown', () => {
    // `ThreadList` links the row of the thread you are already in to `''` — a
    // push to the same path. Keyed on the pathname this did nothing at all:
    // the user tapped a row and the sheet just sat there.
    mount('/thread/a');

    act(() => probe.setOpenMobile(true));
    act(() => probe.navigate('/thread/a'));

    expect(probe.openMobile).toBe(false);
  });

  it('leaves the sheet alone when something merely re-renders', () => {
    // A render for any other reason is not a choice the user made — the guard
    // an effect with no dependency array would throw away.
    const { rerender } = mount('/thread/a');

    act(() => probe.setOpenMobile(true));
    act(() => rerender(tree('/thread/a')));

    expect(probe.openMobile).toBe(true);
  });
});
