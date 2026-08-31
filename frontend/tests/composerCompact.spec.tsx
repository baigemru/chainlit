import { fireEvent, render } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MessageComposer from '@/components/chat/MessageComposer';

import { IAttachment, attachmentsState } from '@/state/chat';

const mockUseIsMobile = vi.fn();
const mockSpontaneousUpload = vi.fn();

vi.mock('@/hooks/use-mobile', () => ({
  useIsMobile: () => mockUseIsMobile()
}));

// The composer's own data, all of it inert: what is under test is where the
// three controls land, not what they do.
vi.mock('@chainlit/react-client', () => ({
  useAuth: () => ({ user: undefined }),
  useChatData: () => ({ askUser: undefined, disabled: false, loading: false }),
  useChatInteract: () => ({
    sendMessage: vi.fn(),
    replyMessage: vi.fn(),
    uploadFile: vi.fn()
  }),
  useChatMessages: () => ({ firstInteraction: undefined }),
  // `features` is read without an optional chain by UploadButton; the upload
  // button is the first thing in the mobile row, so it defaults to enabled —
  // the chevron cases turn it off per test.
  useConfig: () => ({
    config: {
      features: {
        spontaneous_file_upload: { enabled: mockSpontaneousUpload() }
      }
    }
  })
}));

// The return button reaches for session and thread ids that live in the
// mocked client module; it has no parent thread in any case below, so it
// answers for itself.
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

beforeEach(() => {
  vi.clearAllMocks();
  mockSpontaneousUpload.mockReturnValue(true);
});

describe('MessageComposer, compact on a phone', () => {
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

  it('carries the draft across the switch between the two layouts', () => {
    // `useIsMobile` answers false until its effect lands, so a phone renders
    // the desktop branch once and then flips — and the two branches hang the
    // textarea off different parents, which remounts it. Without a re-inject
    // the visible draft is wiped while the composer's own `value` keeps it:
    // an enabled send button over an empty-looking box, sending text nobody
    // can see. A drag across 768px does the same.
    mockUseIsMobile.mockReturnValue(false);

    const { rerender } = renderComposer();
    fireEvent.change(input(), { target: { value: 'draft' } });
    mockUseIsMobile.mockReturnValue(true);
    rerender(
      <RecoilRoot>
        <MessageComposer
          fileSpec={{ maxSizeMb: 500, maxFiles: 20, accept: {} }}
          onFileUpload={noop}
          onFileUploadError={noop}
          autoScrollRef={{ current: true }}
        />
      </RecoilRoot>
    );

    expect((input() as HTMLTextAreaElement).value).toBe('draft');
  });

  it('fills an empty left slot with a chevron', () => {
    // Uploads off and no parent thread: both left buttons render null, and
    // the pill's text would start flush at the rounded edge. The mute ">"
    // holds the slot.
    mockUseIsMobile.mockReturnValue(true);
    mockSpontaneousUpload.mockReturnValue(false);

    renderComposer();

    expect(document.querySelector('#composer-chevron')).not.toBeNull();
    expect(document.querySelector('#upload-button')).toBeNull();
  });

  it('keeps the chevron out while the upload button is there', () => {
    mockUseIsMobile.mockReturnValue(true);

    renderComposer();

    expect(document.querySelector('#composer-chevron')).toBeNull();
    expect(document.querySelector('#upload-button')).not.toBeNull();
  });

  it('never puts the chevron on the desktop card', () => {
    mockUseIsMobile.mockReturnValue(false);
    mockSpontaneousUpload.mockReturnValue(false);

    renderComposer();

    expect(document.querySelector('#composer-chevron')).toBeNull();
  });

  it('leaves the desktop card alone', () => {
    mockUseIsMobile.mockReturnValue(false);

    renderComposer();

    expect(composer().className).toContain('min-h-24');
    // The toolbar row is its own row under the textarea.
    expect(submit().parentElement!.contains(input())).toBe(false);
    expect(submit().className).toContain('h-8');
    expect(submit().className).toContain('w-8');
    expect(submit().className).not.toContain('h-10');
  });
});
