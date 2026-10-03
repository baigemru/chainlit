import { render } from '@testing-library/react';
import { MessageContext, defaultMessageContext } from 'contexts/MessageContext';
import { createContext } from 'react';
import { RecoilRoot, atom } from 'recoil';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { IAction, IStep } from '@chainlit/react-client';

import MessageActions from '@/components/chat/Messages/Message/Buttons/Actions';
import MessagesContainer from '@/components/chat/MessagesContainer';

import { chatBoundariesState } from '@/state/chat';

/**
 * Which `task.indicator` flag an action button in the feed obeys.
 *
 * The spinner (`running`) also counts a run under `cl.run_in_background`,
 * and such a run is exactly the one whose buttons -- "close and carry on in
 * the background", the commands under a partial result -- the person is
 * meant to press while it goes on. Gated on `loading`, every `cl.Action` in
 * the feed went grey for the whole run while the composer beside it stayed
 * open. The gate is the turn (`accepting`), and it reaches the button
 * through `MessageContext`, not the global atom: a kept transcript of a chat
 * that already ended answers to nobody's turn, and a global read would lock
 * its buttons whenever the live chat answers.
 */

const chat = {
  loading: false,
  accepting: true,
  actions: [] as IAction[]
};
let messages: IStep[] = [];

// The package's atoms have to come from the `recoil` the `RecoilRoot` here is
// built by (see `actionVariants.spec.tsx`), and `MessagesContainer` reads a
// dozen hooks whose real versions need a live transport. What is under test
// is the context it hands down, so the hooks are the indicator and nothing
// else.
vi.mock('@chainlit/react-client', () => ({
  MOBILE_BREAKPOINT: 768,
  ChainlitContext: createContext<any>({
    buildEndpoint: (path: string) => path,
    callAction: async () => undefined
  }),
  sessionIdState: atom<string | undefined>({
    key: 'ActionBackgroundRunSessionId',
    default: 'session-1'
  }),
  messagesState: atom<IStep[]>({
    key: 'ActionBackgroundRunMessages',
    default: []
  }),
  updateMessageById: (prev: IStep[]) => prev,
  useChatData: () => ({
    elements: [],
    askUser: undefined,
    loading: chat.loading,
    accepting: chat.accepting,
    actions: chat.actions
  }),
  useChatMessages: () => ({ messages }),
  useChatInteract: () => ({ uploadFile: () => undefined }),
  useChatTransport: () => ({ onMessage: () => () => undefined }),
  useConfig: () => ({ config: undefined }),
  useElementSidebar: () => ({ dispatch: () => undefined })
}));

vi.mock('components/i18n/Translator', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

vi.mock('@/components/chat/MessagesContainer/ChatBoundaryDivider', () => ({
  default: () => null
}));

// The real tree under `Messages` is the step renderer, and none of it stands
// between the context and the button: `MessageButtons` filters by `forId`
// and hands the rest to the real `MessageActions`, which is what this does.
vi.mock('@/components/chat/Messages', () => ({
  Messages: ({
    messages,
    actions
  }: {
    messages: IStep[];
    actions: IAction[];
  }) => (
    <MessageActions
      actions={actions.filter((a) => messages.some((m) => m.id === a.forId))}
    />
  )
}));

const action = (id: string, forId: string): IAction =>
  ({ id, name: id, label: id, forId, payload: {} }) as any;

const step = (id: string): IStep =>
  ({ id, name: 'Assistant', type: 'assistant_message', output: id }) as any;

const button = (id: string) => document.getElementById(id) as HTMLElement;

beforeEach(() => {
  chat.loading = false;
  chat.accepting = true;
  chat.actions = [];
  messages = [];
});

describe('an action button in the feed', () => {
  const renderButton = (ctx: Partial<typeof defaultMessageContext>) =>
    render(
      <RecoilRoot>
        <MessageContext.Provider value={{ ...defaultMessageContext, ...ctx }}>
          <MessageActions actions={[action('go', 'm1')]} />
        </MessageContext.Provider>
      </RecoilRoot>
    );

  it('stays pressable while a background run lights the spinner', () => {
    renderButton({ loading: true, accepting: true });

    expect(button('go')).not.toBeDisabled();
  });

  it('is locked while an ordinary turn holds the conversation', () => {
    renderButton({ loading: true, accepting: false });

    expect(button('go')).toBeDisabled();
  });

  it('reads a context that says nothing about the turn as open', () => {
    // A provider that predates the flag (the read-only thread view) must not
    // lock every button it renders.
    const { accepting: _omitted, ...withoutTurn } = {
      ...defaultMessageContext,
      accepting: undefined
    };
    render(
      <RecoilRoot>
        <MessageContext.Provider value={withoutTurn as never}>
          <MessageActions actions={[action('go', 'm1')]} />
        </MessageContext.Provider>
      </RecoilRoot>
    );

    expect(button('go')).not.toBeDisabled();
  });
});

describe('the feed under a live chat and a kept transcript', () => {
  // `m1` ended a chat at a profile switch and stays on screen above the
  // divider; `m2` is the live chat.
  const renderFeed = () => {
    messages = [step('m1'), step('m2')];
    chat.actions = [action('ended', 'm1'), action('live', 'm2')];
    return render(
      <RecoilRoot
        initializeState={({ set }) =>
          set(chatBoundariesState, [
            { afterMessageId: 'm1', profile: 'Research' }
          ])
        }
      >
        <MessagesContainer />
      </RecoilRoot>
    );
  };

  it('keeps the live chat open during a background run', () => {
    chat.loading = true;
    chat.accepting = true;
    renderFeed();

    expect(button('live')).not.toBeDisabled();
    expect(button('ended')).not.toBeDisabled();
  });

  it('locks only the live chat during an ordinary turn', () => {
    chat.loading = true;
    chat.accepting = false;
    renderFeed();

    expect(button('live')).toBeDisabled();
    // The kept transcript is nobody's turn: reading the global flag would
    // grey these out every time the new chat answers.
    expect(button('ended')).not.toBeDisabled();
  });
});
