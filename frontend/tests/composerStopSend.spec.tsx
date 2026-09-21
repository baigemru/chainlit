import { fireEvent, render } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MessageComposer from '@/components/chat/MessageComposer';

/**
 * Which button the round one is while something is running.
 *
 * It used to be settled by `loading` alone, because a running task locked the
 * composer and there was nothing to send. An application can now declare a
 * run background (`cl.run_in_background`): the spinner stays lit, the
 * composer accepts, and both "stop that" and "send this" become things the
 * click might mean. One button, so it has to be one of them, and the rule is
 * what the box says — empty means Stop, typed means Send.
 */

const mockLoading = vi.fn();
const mockDisabled = vi.fn();
const mockStopTask = vi.fn();
const mockSendMessage = vi.fn();

vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => false
}));

vi.mock('@chainlit/react-client', () => ({
  useAuth: () => ({ user: undefined, data: undefined }),
  useChatData: () => ({
    askUser: undefined,
    accepting: !mockDisabled(),
    disabled: mockDisabled(),
    loading: mockLoading()
  }),
  useChatInteract: () => ({
    sendMessage: mockSendMessage,
    replyMessage: vi.fn(),
    uploadFile: vi.fn(),
    stopTask: mockStopTask
  }),
  // A conversation that has started: `SubmitButton` will not show Stop
  // before the first interaction, because there is nothing to stop.
  useChatMessages: () => ({ firstInteraction: 'hello' }),
  useElementSidebar: () => ({
    state: { slots: [], active: null, visible: false },
    dispatch: vi.fn()
  }),
  useConfig: () => ({
    config: {
      dataPersistence: false,
      features: { spontaneous_file_upload: { enabled: false } }
    }
  })
}));

vi.mock('@/hooks/useParentThread', () => ({
  useParentThreadId: () => undefined
}));

vi.mock('@/hooks/useOpenThread', () => ({
  useOpenThread: () => vi.fn()
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

const renderComposer = () =>
  render(
    <RecoilRoot>
      <MessageComposer
        fileSpec={{ maxSizeMb: 500, maxFiles: 20, accept: {} }}
        onFileUpload={noop}
        onFileUploadError={noop}
        autoScrollRef={{ current: true }}
      />
    </RecoilRoot>
  );

const stop = () => document.querySelector('#stop-button');
const submit = () => document.querySelector('#chat-submit');
const input = () => document.querySelector('#chat-input')!;

const type = (text: string) =>
  fireEvent.input(input(), { target: { innerText: text, textContent: text } });

beforeEach(() => {
  vi.clearAllMocks();
  mockLoading.mockReturnValue(false);
  mockDisabled.mockReturnValue(false);
});

describe('the composer while a background run is going', () => {
  it('offers Stop while the box is empty', () => {
    mockLoading.mockReturnValue(true);

    renderComposer();

    expect(stop()).not.toBeNull();
    expect(submit()).toBeNull();
  });

  it('becomes Send as soon as there is something to send', () => {
    // They typed it to send it, and Enter sends it; a Stop sitting there
    // would be a button disagreeing with the key beside it.
    mockLoading.mockReturnValue(true);

    renderComposer();
    type('the other one, not this');

    expect(submit()).not.toBeNull();
    expect(stop()).toBeNull();
  });

  it('sends it rather than waiting for the run to finish', () => {
    mockLoading.mockReturnValue(true);

    renderComposer();
    type('the other one, not this');
    fireEvent.click(submit()!);

    expect(mockSendMessage).toHaveBeenCalledTimes(1);
    expect(mockStopTask).not.toHaveBeenCalled();
  });

  it('offers Stop and nothing else while the turn is not the user\u2019s', () => {
    // An ordinary run: `disabled` is what the lock is made of, and typing
    // into a locked composer must not send.
    mockLoading.mockReturnValue(true);
    mockDisabled.mockReturnValue(true);

    renderComposer();
    type('too soon');
    const button = submit();

    // Stop, because a locked composer has nothing to send by definition.
    expect(button).toBeNull();
    expect(stop()).not.toBeNull();
    fireEvent.click(stop()!);
    expect(mockStopTask).toHaveBeenCalledTimes(1);
    expect(mockSendMessage).not.toHaveBeenCalled();
  });
});
