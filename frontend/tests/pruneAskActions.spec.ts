import { describe, expect, it } from 'vitest';

import type { IAction } from '../../libs/react-client/src/types';
import type { IAsk } from '../../libs/react-client/src/types';
import { pruneAskActions } from '../../libs/react-client/src/utils/ask';

const action = (id: string): IAction =>
  ({
    id,
    forId: `msg-${id}`,
    name: `name-${id}`,
    payload: {}
  }) as IAction;

const ask = (stepId: string, keys?: string[]): IAsk =>
  ({
    callback: () => undefined,
    spec: {
      type: keys ? 'action' : 'text',
      stepId,
      timeout: 60,
      ...(keys ? { keys } : {})
    }
  }) as IAsk;

describe('pruneAskActions', () => {
  it("removes the previous ask's keyed actions when a foreign ask replaces it", () => {
    const actions = [action('a1'), action('a2'), action('plain')];
    const prev = ask('step-old', ['a1', 'a2']);

    const result = pruneAskActions(actions, prev, 'step-new');

    expect(result.map((a) => a.id)).toEqual(['plain']);
  });

  it('keeps the actions when the incoming ask is the SAME ask (reconnect restore)', () => {
    // The server re-emits actions first, then the ask, for the same
    // step — removal here would erase the just-re-emitted buttons and
    // regress the 2.11.34 restore fix.
    const actions = [action('a1'), action('a2')];
    const prev = ask('step-same', ['a1', 'a2']);

    const result = pruneAskActions(actions, prev, 'step-same');

    expect(result).toBe(actions);
  });

  it('removes keyed actions on clear/timeout (no incoming step id)', () => {
    const actions = [action('a1'), action('plain')];
    const prev = ask('step-old', ['a1']);

    const result = pruneAskActions(actions, prev);

    expect(result.map((a) => a.id)).toEqual(['plain']);
  });

  it('never touches regular message actions outside the previous ask keys', () => {
    const actions = [action('plain-1'), action('plain-2')];
    const prev = ask('step-old', ['a1', 'a2']);

    const result = pruneAskActions(actions, prev);

    expect(result).toBe(actions);
  });

  it('is a no-op without a previous ask', () => {
    const actions = [action('a1')];

    expect(pruneAskActions(actions, undefined)).toBe(actions);
    expect(pruneAskActions(actions, undefined, 'step-new')).toBe(actions);
  });

  it('is a no-op for asks without action keys (text/file/element)', () => {
    const actions = [action('a1')];
    const prev = ask('step-old');

    expect(pruneAskActions(actions, prev)).toBe(actions);
  });
});
