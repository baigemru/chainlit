import { render } from '@testing-library/react';
import { RecoilRoot } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { IMessageElement } from '@chainlit/react-client';

import MessagesContainer from '@/components/chat/MessagesContainer';

/**
 * The feed has exactly one thing to say about the element panel: a click.
 *
 * It used to have two. An effect watched `elementState` for `display: 'side'`
 * elements and opened the panel by itself, which made it the panel's second
 * writer: `set_title("Shortlist")` followed by a PDF arriving retitled the
 * panel after the PDF, and the next `set_elements` read that title back out
 * of the atom. Deleting it is a behaviour change — a side element no longer
 * appears without being asked for — and this is where that is stated.
 */

const mockDispatch = vi.fn();
let elements: IMessageElement[] = [];

vi.mock('@chainlit/react-client', async () => {
  // Real atoms, faked hooks: `MessagesContainer` writes to `messagesState`
  // through Recoil, and a plain object there is not an atom.
  const state = await vi.importActual<
    typeof import('../../libs/react-client/src/state')
  >('../../libs/react-client/src/state');
  const { createContext } =
    await vi.importActual<typeof import('react')>('react');
  return {
    ...state,
    ChainlitContext: createContext({
      setFeedback: vi.fn(),
      deleteFeedback: vi.fn()
    }),
    useChatData: () => ({
      elements,
      askUser: undefined,
      loading: false,
      actions: []
    }),
    useChatMessages: () => ({ messages: [] }),
    useChatInteract: () => ({ uploadFile: vi.fn() }),
    useChatTransport: () => ({ onMessage: () => () => undefined }),
    useConfig: () => ({ config: { ui: {}, features: {} } }),
    useElementSidebar: () => ({
      state: { slots: [], active: null, visible: false },
      dispatch: mockDispatch
    }),
    updateMessageById: (steps: unknown) => steps
  };
});

// The transcript itself is not the subject; what is, is the callback the
// feed hands its elements. `Messages` is replaced by a probe that calls it.
let onElementRefClick: ((element: IMessageElement) => void) | undefined;
vi.mock('@/components/chat/Messages', () => ({
  Messages: () => null
}));
vi.mock('@/contexts/MessageContext', async () => {
  const { createContext } =
    await vi.importActual<typeof import('react')>('react');
  const context = createContext<Record<string, never>>({} as never);
  const Provider = context.Provider;
  return {
    MessageContext: {
      ...context,
      Provider: ({ value, children }: never) => {
        onElementRefClick = (value as never as Record<string, never>)[
          'onElementRefClick'
        ];
        return <Provider value={value}>{children}</Provider>;
      }
    }
  };
});

vi.mock('components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

const sideElement = (id: string): IMessageElement =>
  ({ id, name: `el-${id}`, type: 'text', display: 'side' }) as IMessageElement;

beforeEach(() => {
  vi.clearAllMocks();
  onElementRefClick = undefined;
  elements = [];
});

describe('a display="side" element in the feed', () => {
  it('does not open the panel by merely existing', () => {
    elements = [sideElement('e1')];

    render(
      <RecoilRoot>
        <MessagesContainer />
      </RecoilRoot>
    );

    expect(mockDispatch).not.toHaveBeenCalled();
  });

  it('opens as a preview when it is clicked', () => {
    const element = sideElement('e1');
    elements = [element];

    render(
      <RecoilRoot>
        <MessagesContainer />
      </RecoilRoot>
    );
    onElementRefClick!(element);

    expect(mockDispatch).toHaveBeenCalledWith({ op: 'preview', element });
  });

  it('previews a page element too when there is nowhere to navigate', () => {
    const element = {
      ...sideElement('e2'),
      display: 'page'
    } as IMessageElement;
    elements = [element];

    render(
      <RecoilRoot>
        <MessagesContainer />
      </RecoilRoot>
    );
    onElementRefClick!(element);

    expect(mockDispatch).toHaveBeenCalledWith({ op: 'preview', element });
  });
});
