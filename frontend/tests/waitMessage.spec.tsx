import {
  clampWaitIntervalMs,
  getActiveWaitStepId,
  isWaitActive,
  nextWaitIndex
} from '@/lib/waitMessage';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IStep, IStepWait } from '@chainlit/react-client';
import { addMessage, updateMessageById } from '@chainlit/react-client';

import { useWaitDisplayText } from '@/hooks/useWaitDisplayText';

const makeStep = (partial: Partial<IStep> & { id: string }): IStep => ({
  name: 'step',
  type: 'assistant_message',
  output: '',
  createdAt: '2026-08-22T00:00:00',
  ...partial
});

describe('clampWaitIntervalMs', () => {
  it('defaults to 5000 when the interval is missing or invalid', () => {
    expect(clampWaitIntervalMs(undefined)).toBe(5000);
    expect(clampWaitIntervalMs(NaN)).toBe(5000);
    expect(clampWaitIntervalMs(Infinity)).toBe(5000);
  });

  it('clamps to the 2000ms minimum', () => {
    expect(clampWaitIntervalMs(0)).toBe(2000);
    expect(clampWaitIntervalMs(500)).toBe(2000);
    expect(clampWaitIntervalMs(1999)).toBe(2000);
  });

  it('keeps valid intervals as-is', () => {
    expect(clampWaitIntervalMs(2000)).toBe(2000);
    expect(clampWaitIntervalMs(8000)).toBe(8000);
  });
});

describe('nextWaitIndex', () => {
  it('advances through the list', () => {
    expect(nextWaitIndex(0, 3, false)).toBe(1);
    expect(nextWaitIndex(1, 3, false)).toBe(2);
  });

  it('holds on the last text without loop', () => {
    expect(nextWaitIndex(2, 3, false)).toBe(2);
  });

  it('cycles back to the first text with loop', () => {
    expect(nextWaitIndex(2, 3, true)).toBe(0);
  });

  it('is stable for empty and single-item lists', () => {
    expect(nextWaitIndex(0, 0, false)).toBe(0);
    expect(nextWaitIndex(0, 0, true)).toBe(0);
    expect(nextWaitIndex(0, 1, false)).toBe(0);
    expect(nextWaitIndex(0, 1, true)).toBe(0);
  });
});

describe('getActiveWaitStepId', () => {
  const wait: IStepWait = { texts: ['a', 'b'] };

  it('returns undefined for an empty conversation', () => {
    expect(getActiveWaitStepId([])).toBeUndefined();
  });

  it('is undefined when the last step carries no wait (stable for apps without wait messages)', () => {
    const messages = [makeStep({ id: 'a' }), makeStep({ id: 'b' })];
    expect(getActiveWaitStepId(messages)).toBeUndefined();
  });

  it('returns the last root step when it carries wait', () => {
    const messages = [makeStep({ id: 'a' }), makeStep({ id: 'b', wait })];
    expect(getActiveWaitStepId(messages)).toBe('b');
  });

  it('descends into the last root step (wait message nested under a run)', () => {
    const messages = [
      makeStep({ id: 'user', type: 'user_message' }),
      makeStep({
        id: 'run',
        name: 'on_message',
        type: 'run',
        steps: [
          makeStep({ id: 'tool', type: 'tool' }),
          makeStep({
            id: 'loader',
            wait,
            steps: []
          })
        ]
      })
    ];
    expect(getActiveWaitStepId(messages)).toBe('loader');
  });

  it('ignores a wait step that is no longer the last one', () => {
    const messages = [
      makeStep({ id: 'loader', wait }),
      makeStep({ id: 'newer' })
    ];
    expect(getActiveWaitStepId(messages)).toBeUndefined();
  });
});

describe('isWaitActive', () => {
  const wait: IStepWait = { texts: ['a', 'b'], intervalMs: 2000, loop: false };

  it('is active when the step has wait and is the active wait step', () => {
    const step = makeStep({ id: 'loader', wait });
    expect(isWaitActive(step, 'loader')).toBe(true);
  });

  it('deactivates when another step is the active one', () => {
    const step = makeStep({ id: 'loader', wait });
    expect(isWaitActive(step, 'newer')).toBe(false);
  });

  it('is inactive without a wait field or without an active wait step', () => {
    // Even on an id match (e.g. a kept transcript copy of the live wait
    // step) a step without its own `wait` never shimmers.
    expect(isWaitActive(makeStep({ id: 'loader' }), 'loader')).toBe(false);
    expect(isWaitActive(makeStep({ id: 'loader', wait }), undefined)).toBe(
      false
    );
  });
});

describe('update_message semantics', () => {
  it('an update without wait clears the stored wait (handler spreads it explicitly)', () => {
    const stored = makeStep({
      id: 'loader',
      output: 'old',
      wait: { texts: ['a', 'b'] }
    });
    const incoming = makeStep({ id: 'loader', output: 'final' });

    // Mirrors the update_message handler in useChatSession: updateMessageById
    // merges fields, so the handler spreads the (possibly undefined) wait.
    const next = updateMessageById([stored], 'loader', {
      ...incoming,
      wait: incoming.wait
    });

    expect(next[0].output).toBe('final');
    expect(next[0].wait).toBeUndefined();
  });

  it('an update with a new wait replaces the stored one', () => {
    const stored = makeStep({ id: 'loader', wait: { texts: ['a'] } });
    const incoming = makeStep({
      id: 'loader',
      wait: { texts: ['phase 2', 'phase 3'], loop: true }
    });

    const next = updateMessageById([stored], 'loader', {
      ...incoming,
      wait: incoming.wait
    });

    expect(next[0].wait).toEqual({ texts: ['phase 2', 'phase 3'], loop: true });
  });

  it('a re-send / stream_start for a known id without wait clears the stored wait', () => {
    const stored = makeStep({
      id: 'loader',
      output: 'loading',
      wait: { texts: ['a', 'b'] }
    });
    const incoming = makeStep({
      id: 'loader',
      output: '',
      streaming: true
    });

    // Mirrors the new_message/stream_start handlers in useChatSession: for a
    // known id addMessage merges too, so they spread the explicit wait.
    const next = addMessage([stored], { ...incoming, wait: incoming.wait });

    expect(next[0].streaming).toBe(true);
    expect(next[0].wait).toBeUndefined();
  });
});

const Probe = ({ wait }: { wait?: IStepWait }) => {
  const text = useWaitDisplayText(wait);
  return <div data-testid="wait-text">{text ?? '<none>'}</div>;
};

describe('useWaitDisplayText', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const advance = (ms: number) => {
    act(() => {
      vi.advanceTimersByTime(ms);
    });
  };

  it('returns nothing without wait or with empty texts (shimmer only)', () => {
    const { rerender } = render(<Probe wait={undefined} />);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('<none>');

    rerender(<Probe wait={{ texts: [] }} />);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('<none>');
    advance(60000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('<none>');
  });

  it('rotates and holds on the last text without loop', () => {
    render(
      <Probe wait={{ texts: ['one', 'two', 'three'], intervalMs: 2000 }} />
    );
    expect(screen.getByTestId('wait-text')).toHaveTextContent('one');
    advance(2000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('two');
    advance(2000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('three');
    advance(60000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('three');
  });

  it('cycles with loop', () => {
    render(
      <Probe wait={{ texts: ['one', 'two'], intervalMs: 2000, loop: true }} />
    );
    expect(screen.getByTestId('wait-text')).toHaveTextContent('one');
    advance(2000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('two');
    advance(2000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('one');
  });

  it('clamps sub-minimum intervals to 2000ms', () => {
    render(<Probe wait={{ texts: ['one', 'two'], intervalMs: 500 }} />);
    advance(1999);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('one');
    advance(1);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('two');
  });

  it('restarts from the first text when the wait object changes', () => {
    const { rerender } = render(
      <Probe wait={{ texts: ['one', 'two'], intervalMs: 2000 }} />
    );
    advance(2000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('two');

    rerender(<Probe wait={{ texts: ['alpha', 'beta'], intervalMs: 2000 }} />);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('alpha');
    advance(2000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('beta');
  });

  it('stops rotating once deactivated (wait removed)', () => {
    const { rerender } = render(
      <Probe wait={{ texts: ['one', 'two'], intervalMs: 2000 }} />
    );
    rerender(<Probe wait={undefined} />);
    advance(60000);
    expect(screen.getByTestId('wait-text')).toHaveTextContent('<none>');
  });
});
