import { render, screen } from '@testing-library/react';
import { MessageContext, defaultMessageContext } from 'contexts/MessageContext';
import { describe, expect, it, vi } from 'vitest';

import type { IStep } from '@chainlit/react-client';

import { Messages } from '@/components/chat/Messages';

vi.mock('@/components/i18n', () => ({
  Translator: ({ path, options }: { path: string; options?: any }) => (
    <span>
      {path}
      {options?.count !== undefined ? `:${options.count}` : ''}
    </span>
  )
}));

vi.mock('@/components/chat/Messages/Message/Avatar', () => ({
  MessageAvatar: ({ author }: { author: string }) => (
    <span>{`avatar:${author}`}</span>
  )
}));

vi.mock('@/components/chat/Messages/Message/Content', () => ({
  MessageContent: ({ message }: { message: IStep }) => (
    <div>{`content:${message.output}`}</div>
  )
}));

vi.mock('@/components/chat/Messages/Message/Buttons', () => ({
  MessageButtons: () => null
}));

vi.mock('@/components/chat/Messages/Message/AskActionButtons', () => ({
  AskActionButtons: () => null
}));

vi.mock('@/components/chat/Messages/Message/AskFileButton', () => ({
  AskFileButton: () => null
}));

vi.mock('@/components/chat/Messages/Message/UserMessage', () => ({
  default: ({ children }: any) => <div>{children}</div>
}));

vi.mock('hooks/useLayoutMaxWidth', () => ({
  useLayoutMaxWidth: () => '48rem'
}));

let stepId = 0;

const makeStep = (partial: Partial<IStep>): IStep => ({
  id: `step-${stepId++}`,
  name: 'step',
  type: 'tool',
  threadId: 't1',
  input: '',
  output: '',
  createdAt: '2026-07-11T00:00:00',
  start: '2026-07-11T00:00:00',
  end: '2026-07-11T00:00:01',
  steps: [],
  ...partial
});

const toolStep = (name: string, steps: IStep[] = []): IStep =>
  makeStep({ name, type: 'tool', input: '{}', output: 'ok', steps });

const assistantMessage = (output: string, steps: IStep[] = []): IStep =>
  makeStep({ name: 'PANDAPOISK', type: 'assistant_message', output, steps });

const renderTree = (messages: IStep[]) =>
  render(
    <MessageContext.Provider
      value={{
        ...defaultMessageContext,
        cot: 'full',
        cotDisplay: 'compact',
        showStepDetails: true
      }}
    >
      <Messages
        messages={messages}
        elements={[]}
        actions={[]}
        indent={0}
        isRunning={false}
      />
    </MessageContext.Provider>
  );

describe('compact CoT display', () => {
  it('collapses consecutive tool steps nested under an assistant message', () => {
    const run = makeStep({
      name: 'on_message',
      type: 'run',
      steps: [
        assistantMessage('Промежуточное сообщение', [
          toolStep('read_file'),
          toolStep('read_file')
        ]),
        assistantMessage('Финальный ответ')
      ]
    });
    renderTree([run]);

    expect(
      screen.getByText('content:Промежуточное сообщение')
    ).toBeInTheDocument();
    expect(screen.getByText('content:Финальный ответ')).toBeInTheDocument();
    expect(
      screen.getByText(/chat.messages.status.usedSteps:2/)
    ).toBeInTheDocument();
  });

  it('does not swallow a run nested under a user message into a compact block', () => {
    const userMessage = makeStep({
      name: 'user',
      type: 'user_message',
      output: 'Посмотри сколько в разных валютах стоит',
      steps: [
        makeStep({
          name: 'on_message',
          type: 'run',
          steps: [
            toolStep('get_exchange_rate'),
            assistantMessage('Вот стоимость всех адаптеров')
          ]
        })
      ]
    });
    renderTree([userMessage]);

    // The answer must be visible, not hidden inside a collapsed accordion
    const answer = screen.getByText('content:Вот стоимость всех адаптеров');
    expect(answer).toBeInTheDocument();
    expect(answer.closest('[hidden]')).toBeNull();

    // A single tool step renders as a plain step, no "Used N steps" block
    expect(
      screen.queryByText(/chat.messages.status.usedSteps/)
    ).not.toBeInTheDocument();
    expect(screen.getByText(/chat.messages.status.used$/)).toBeInTheDocument();
  });

  it('collapses consecutive run-level steps but keeps messages visible', () => {
    const run = makeStep({
      name: 'on_message',
      type: 'run',
      steps: [
        toolStep('ls'),
        toolStep('read_file'),
        toolStep('get_exchange_rate'),
        assistantMessage('Ответ')
      ]
    });
    renderTree([run]);

    expect(
      screen.getByText(/chat.messages.status.usedSteps:3/)
    ).toBeInTheDocument();
    const answer = screen.getByText('content:Ответ');
    expect(answer.closest('[hidden]')).toBeNull();
  });
});
