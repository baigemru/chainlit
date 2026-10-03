import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatFooter from '@/components/chat/Footer';

/**
 * The line the server puts under the composer in a conversation that has
 * started. The welcome screen's hint is `welcomeHint.spec.tsx`'s subject;
 * this is the footer's, which only ever draws the server's words — the
 * profile's hint explains an empty composer, and this one is past that.
 */

const mockComposer = vi.fn();
const mockMessages = vi.fn();

vi.mock('@chainlit/react-client', () => ({
  useChatData: () => ({ composer: mockComposer() }),
  useChatMessages: () => ({ messages: mockMessages() }),
  useConfig: () => ({ config: { features: {} } })
}));

vi.mock('@/components/chat/MessageComposer', () => ({
  default: () => <div id="message-composer">composer</div>
}));
vi.mock('@/components/WaterMark', () => ({
  default: () => <div className="watermark">watermark</div>
}));
vi.mock('@/components/Markdown', () => ({
  Markdown: ({ children }: { children: string }) => <div>{children}</div>
}));

const props = {
  fileSpec: { accept: [] } as any,
  onFileUpload: () => undefined,
  onFileUploadError: () => undefined,
  autoScrollRef: { current: true }
};

const started = [{ id: 'm1', type: 'user_message', output: 'hi' }];

beforeEach(() => {
  vi.clearAllMocks();
  mockMessages.mockReturnValue(started);
  mockComposer.mockReturnValue({});
});

describe('the footer hint', () => {
  it('draws what the server said, between the composer and the watermark', () => {
    mockComposer.mockReturnValue({ hint: '41 of 60 left' });

    const { container } = render(<ChatFooter {...props} />);

    const hint = screen.getByText('41 of 60 left').closest('.composer-hint')!;
    expect(hint).not.toBeNull();
    const order = Array.from(container.firstElementChild!.children);
    expect(order.indexOf(hint)).toBe(
      order.indexOf(container.querySelector('#message-composer')!) + 1
    );
    expect(order.indexOf(hint)).toBe(
      order.indexOf(container.querySelector('.watermark')!) - 1
    );
  });

  it('draws nothing while the server has said nothing', () => {
    const { container } = render(<ChatFooter {...props} />);

    expect(container.querySelector('.composer-hint')).toBeNull();
  });

  it('draws nothing for an empty hint', () => {
    mockComposer.mockReturnValue({ hint: '' });

    const { container } = render(<ChatFooter {...props} />);

    expect(container.querySelector('.composer-hint')).toBeNull();
  });
});
