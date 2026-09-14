import { fireEvent, render } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MessageComposer from '@/components/chat/MessageComposer';

/**
 * The draft has to outlive the composer that is showing it.
 *
 * `/` and `/thread/:id` are two route elements, so ThreadAddressListener
 * moving the address onto the thread the server just named unmounts `Home`
 * and mounts `Page` — a second `MessageComposer`, and a third after New
 * Chat sends the address back to `/` and the next `session.ready` names it
 * again. Anything the user typed in between is in flight across those
 * remounts, which is why the draft lives in Recoil and why `clear()` must
 * not touch it.
 */

const mockSendMessage = vi.fn();

vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => false
}));

vi.mock('@chainlit/react-client', () => ({
  useAuth: () => ({ user: undefined }),
  useChatData: () => ({ askUser: undefined, disabled: false, loading: false }),
  useChatInteract: () => ({
    sendMessage: mockSendMessage,
    replyMessage: vi.fn(),
    uploadFile: vi.fn()
  }),
  useChatMessages: () => ({ firstInteraction: undefined }),
  useConfig: () => ({
    config: { features: { spontaneous_file_upload: { enabled: true } } }
  })
}));

vi.mock('@/hooks/useParentThread', () => ({
  useParentThreadId: () => undefined
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    ready: true,
    i18n: { exists: () => true }
  })
}));

const noop = () => undefined;

const composer = (key: string) => (
  <MessageComposer
    key={key}
    fileSpec={{ maxSizeMb: 500, maxFiles: 20, accept: {} }}
    onFileUpload={noop}
    onFileUploadError={noop}
    autoScrollRef={{ current: true }}
  />
);

const input = () =>
  document.querySelector('#chat-input') as HTMLTextAreaElement;
const submit = () =>
  document.querySelector('#chat-submit') as HTMLButtonElement;

beforeEach(() => {
  vi.clearAllMocks();
});

describe('the composer draft across a remount', () => {
  it('comes back with the text the user had typed', () => {
    // A changed key is a remount of the composer alone; the RecoilRoot above
    // it is the store the route change leaves standing.
    const { rerender } = render(<RecoilRoot>{composer('a')}</RecoilRoot>);
    fireEvent.change(input(), { target: { value: 'half a question' } });

    rerender(<RecoilRoot>{composer('b')}</RecoilRoot>);

    expect(input().value).toBe('half a question');
    // And the send button agrees with what is on screen, which is the whole
    // failure mode: the two used to disagree in the other direction.
    expect(submit()).not.toBeDisabled();
  });

  it('is empty again once the message has been sent', () => {
    const { rerender } = render(<RecoilRoot>{composer('a')}</RecoilRoot>);
    fireEvent.change(input(), { target: { value: 'sent' } });

    fireEvent.click(submit());
    expect(mockSendMessage).toHaveBeenCalledTimes(1);

    rerender(<RecoilRoot>{composer('b')}</RecoilRoot>);

    expect(input().value).toBe('');
    expect(submit()).toBeDisabled();
  });

  it('starts empty when nothing was typed', () => {
    render(<RecoilRoot>{composer('a')}</RecoilRoot>);

    expect(input().value).toBe('');
    expect(submit()).toBeDisabled();
  });
});
