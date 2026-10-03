import { render, screen } from '@testing-library/react';
import { createContext } from 'react';
import { RecoilRoot } from 'recoil';
import { describe, expect, it, vi } from 'vitest';

import type { IStarter } from '@chainlit/react-client';

import Starter from '@/components/chat/Starter';

/**
 * A plate's description keeps the application's line breaks.
 *
 * A plate is the one density with room for two lines of explanation — what
 * the errand does, and what comes out of it — and the application separates
 * them with `\n`. The button carries `whitespace-normal`, which folds that
 * newline into a space, so the description has to say otherwise for itself.
 */

vi.mock('@chainlit/react-client', () => ({
  MOBILE_BREAKPOINT: 768,
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path
  }),
  useAuth: () => ({ user: { identifier: 'someone' } }),
  useChatData: () => ({ loading: false, connected: true }),
  useChatInteract: () => ({ sendMessage: vi.fn(), clear: vi.fn() }),
  useChatSession: () => ({ chatProfile: undefined, setChatProfile: vi.fn() })
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

const plate = (description: string, layout: 'plates' | 'rows' = 'plates') => {
  const starter: IStarter = { label: 'Buyer', message: 'Buyer', description };
  return render(
    <RecoilRoot>
      <Starter starter={starter} layout={layout} />
    </RecoilRoot>
  );
};

describe('a plate’s description', () => {
  it('keeps its line breaks', () => {
    plate('I research the product.\nYou get: a shortlist');

    const description = screen.getByText(/I research the product\./);
    // The newline reaches the DOM as written, and the class is what makes a
    // browser draw it as one.
    expect(description.textContent).toBe(
      'I research the product.\nYou get: a shortlist'
    );
    expect(description.className).toContain('whitespace-pre-line');
  });

  it('is the only density that does', () => {
    // A row is one line by design; a break there would make it two.
    plate('trust score', 'rows');

    expect(screen.getByText('trust score').className).not.toContain(
      'whitespace-pre-line'
    );
  });
});
