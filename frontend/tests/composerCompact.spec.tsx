import { fireEvent, render } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MessageComposer from '@/components/chat/MessageComposer';

import { IAttachment, attachmentsState } from '@/state/chat';

const mockUseIsMobile = vi.fn();
const mockSpontaneousUpload = vi.fn();
const mockSidebarAvailable = vi.fn();
const mockParentThreadId = vi.fn();
const mockSidebarDispatch = vi.fn();
const mockComposer = vi.fn();

vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => mockUseIsMobile()
}));

// The composer's own data, all of it inert: what is under test is where the
// three controls land, not what they do.
vi.mock('@chainlit/react-client', () => ({
  // A login and somewhere to persist to is what `useHasLeftSidebar` asks for;
  // one switch drives both, because what the cases care about is whether a
  // thread history exists at all, not which half of the predicate is missing.
  useAuth: () => ({
    user: undefined,
    data: mockSidebarAvailable() ? { requireLogin: true } : undefined
  }),
  useChatData: () => ({
    askUser: undefined,
    composer: mockComposer(),
    disabled: false,
    loading: false
  }),
  useChatInteract: () => ({
    sendMessage: vi.fn(),
    replyMessage: vi.fn(),
    uploadFile: vi.fn()
  }),
  useChatMessages: () => ({ firstInteraction: undefined }),
  useElementSidebar: () => ({
    state: { slots: [], active: null, visible: false },
    dispatch: mockSidebarDispatch
  }),
  // `features` is read without an optional chain by UploadButton; the upload
  // button is the first thing in the mobile row, so it defaults to enabled —
  // the chevron cases turn it off per test.
  useConfig: () => ({
    config: {
      dataPersistence: mockSidebarAvailable(),
      features: {
        spontaneous_file_upload: { enabled: mockSpontaneousUpload() }
      }
    }
  })
}));

// The hook reaches for session and thread ids that live in the mocked client
// module, and for the config flag that gates it; what the composer does with
// its answer is the subject here, so the answer is handed to it directly. The
// flag itself is tested where it is defined, in `parentThreadFlag.spec.tsx`.
vi.mock('@/hooks/useParentThread', () => ({
  useParentThreadId: () => mockParentThreadId()
}));

// The return button's own hook reaches for the router and the API client.
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

const renderComposer = (attachments: IAttachment[] = []) =>
  render(
    <RecoilRoot
      initializeState={({ set }) => set(attachmentsState, attachments)}
    >
      <MessageComposer
        fileSpec={{ maxSizeMb: 500, maxFiles: 20, accept: {} }}
        onFileUpload={noop}
        onFileUploadError={noop}
        autoScrollRef={{ current: true }}
      />
    </RecoilRoot>
  );

const composer = () => document.querySelector('#message-composer')!;
const submit = () => document.querySelector('#chat-submit')!;
const input = () => document.querySelector('#chat-input')!;
const chevron = () => document.querySelector('#composer-chevron');

beforeEach(() => {
  vi.clearAllMocks();
  mockSpontaneousUpload.mockReturnValue(true);
  mockSidebarAvailable.mockReturnValue(false);
  mockParentThreadId.mockReturnValue(undefined);
  mockComposer.mockReturnValue({});
});

describe('MessageComposer, one pill on every width', () => {
  it('drops the card height and puts the controls on the textarea row', () => {
    mockUseIsMobile.mockReturnValue(true);

    renderComposer();

    expect(composer().className).not.toContain('min-h-24');
    // `Input` wraps its textarea in a positioning div of its own, so the row
    // is the submit button's parent and the textarea is a grandchild — being
    // in the same row at all is what desktop never is.
    const row = submit().parentElement!;
    expect(row.contains(input())).toBe(true);
    // Pinned to the bottom edge: a grown textarea must push the buttons down,
    // not centre them against it.
    expect(row.className).toContain('items-end');
  });

  it('centres a single line of text on the row the icons sit on', () => {
    mockUseIsMobile.mockReturnValue(false);

    renderComposer();

    // Read off the classes because jsdom lays nothing out: `scrollHeight` is
    // 0 here. The row is bottom-aligned and 40px tall, so a line is level
    // with the icons only if the textarea pads it equally above and below:
    // 8 + 24 + 8. Unpadded, the text sat in the top 24px of the box.
    const c = input().className.split(/\s+/);
    expect(c).toEqual(
      expect.arrayContaining(['py-2', 'leading-6', 'h-[40px]'])
    );
    for (const cls of ['p-0', 'py-0', 'pt-0', 'pb-0']) {
      expect(c).not.toContain(cls);
    }
  });

  it('gives the send button a thumb-sized tap target', () => {
    mockUseIsMobile.mockReturnValue(true);

    renderComposer();

    expect(submit().className).toContain('h-10');
    expect(submit().className).toContain('w-10');
    expect(submit().className).not.toContain('h-8');
  });

  it('keeps the attachments above the row', () => {
    mockUseIsMobile.mockReturnValue(true);

    renderComposer([
      { id: 'a', name: 'plan.pdf', size: 12, type: 'application/pdf' }
    ]);

    const attachments = composer().querySelector('#attachments');
    expect(attachments).not.toBeNull();
    // Above, not inside: the row stays one line tall whatever is attached.
    expect(composer().firstElementChild!.contains(attachments!)).toBe(true);
    expect(submit().parentElement!.contains(attachments!)).toBe(false);
  });

  it('keeps the chevron in the left slot whatever else is there', () => {
    // Always, on both layouts. The panel is the one control here that is
    // always available -- with nothing in it the chevron opens an empty one,
    // which is a legal state -- and a control that comes and goes teaches
    // nobody it exists.
    mockUseIsMobile.mockReturnValue(true);
    mockSpontaneousUpload.mockReturnValue(true);
    mockParentThreadId.mockReturnValue('parent-thread');

    renderComposer();

    expect(chevron()).not.toBeNull();
    expect(document.querySelector('#upload-button')).not.toBeNull();
    expect(document.querySelector('#open-parent-thread')).not.toBeNull();
  });

  it('renders the chevron with an empty left slot too', () => {
    mockUseIsMobile.mockReturnValue(true);
    mockSpontaneousUpload.mockReturnValue(false);

    renderComposer();

    expect(chevron()).not.toBeNull();
    expect(document.querySelector('#upload-button')).toBeNull();
  });

  it('puts the chevron last in the row, after the other two', () => {
    // The buttons whose position people have learned must not shift when a
    // panel appears or goes away.
    mockUseIsMobile.mockReturnValue(true);
    mockSpontaneousUpload.mockReturnValue(true);
    mockParentThreadId.mockReturnValue('parent-thread');

    renderComposer();

    const row = Array.from(chevron()!.parentElement!.children);
    expect(row.indexOf(document.querySelector('#upload-button')!)).toBeLessThan(
      row.indexOf(chevron()!)
    );
    expect(
      row.indexOf(document.querySelector('#open-parent-thread')!)
    ).toBeLessThan(row.indexOf(chevron()!));
  });

  it('opens the element panel when it is clicked', () => {
    mockUseIsMobile.mockReturnValue(true);

    renderComposer();
    fireEvent.click(chevron()!);

    expect(mockSidebarDispatch).toHaveBeenCalledWith({ op: 'show' });
  });

  it('is the same button as the paperclip: variant, size and hover', () => {
    // A bare <button> of its own 32px box sat off the paperclip's centre and
    // lit nothing on hover; the two must be one kind of control.
    for (const mobile of [true, false]) {
      mockUseIsMobile.mockReturnValue(mobile);
      const { unmount } = renderComposer();

      const own = chevron()!.className.split(/\s+/);
      const clip = document
        .querySelector('#upload-button')!
        .className.split(/\s+/);
      expect(own).toEqual(
        expect.arrayContaining(['h-9', 'w-9', 'hover:bg-muted'])
      );
      expect(own.sort()).toEqual(clip.sort());
      unmount();
    }
  });

  it('centres the left controls in a slot one line of text tall', () => {
    mockUseIsMobile.mockReturnValue(true);
    mockParentThreadId.mockReturnValue('parent-thread');

    renderComposer();

    const slot = chevron()!.parentElement!;
    for (const cls of ['flex', 'h-10', 'items-center']) {
      expect(slot.className.split(/\s+/)).toContain(cls);
    }
    expect(slot.contains(document.querySelector('#upload-button'))).toBe(true);
    expect(slot.contains(document.querySelector('#open-parent-thread'))).toBe(
      true
    );
    expect(slot.contains(input())).toBe(false);
  });

  it('names itself for a screen reader', () => {
    mockUseIsMobile.mockReturnValue(true);

    renderComposer();

    expect(chevron()!.tagName).toBe('BUTTON');
    expect(chevron()!.getAttribute('aria-hidden')).toBeNull();
    expect(chevron()!.getAttribute('aria-label')).toBe('Open the side panel');
  });
});

describe('MessageComposer on a wide screen', () => {
  it('draws the same pill as on a phone', () => {
    // No card with a toolbar row: the welcome screen and the chat's footer
    // both wanted the pill, and nothing else draws a composer.
    mockUseIsMobile.mockReturnValue(false);

    renderComposer();

    expect(composer().className).not.toContain('min-h-24');
    expect(submit().parentElement!.contains(input())).toBe(true);
    expect(submit().className).toContain('h-10');
    expect(submit().parentElement!.contains(chevron())).toBe(true);
  });
});

describe('the placeholder', () => {
  it('is the translation while the server has said nothing', () => {
    mockUseIsMobile.mockReturnValue(false);

    renderComposer();

    expect(input().getAttribute('placeholder')).toBe('chat.input.placeholder');
  });

  it('is the conversation’s own once the server has said one', () => {
    // `composer.state`: an application explaining what typing does now —
    // a correction to a running job rather than a new question.
    mockUseIsMobile.mockReturnValue(false);
    mockComposer.mockReturnValue({ placeholder: 'Add a correction' });

    renderComposer();

    expect(input().getAttribute('placeholder')).toBe('Add a correction');
  });
});
