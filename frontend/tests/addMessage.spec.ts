import { describe, expect, it } from 'vitest';

import type { IStep } from '../../libs/react-client/src/types';
import { addMessage } from '../../libs/react-client/src/utils/message';

const step = (id: string, parentId?: string): IStep =>
  ({
    id,
    parentId,
    name: 'test',
    type: 'assistant_message',
    output: `output-${id}`,
    createdAt: '2026-08-16T00:00:00Z'
  }) as IStep;

describe('addMessage', () => {
  it('appends a message without parent at the top level', () => {
    const result = addMessage([], step('a'));
    expect(result.map((m) => m.id)).toEqual(['a']);
  });

  it('nests a message under its parent when the parent is present', () => {
    const parent = step('parent');
    const result = addMessage([parent], step('child', 'parent'));

    expect(result).toHaveLength(1);
    expect(result[0].steps?.map((m) => m.id)).toEqual(['child']);
  });

  it('renders an orphan top-level instead of dropping it', () => {
    // The parent step is gone from the UI state (page reload while the
    // server session lives on): the child must still render.
    const result = addMessage([], step('orphan', 'missing-parent'));

    expect(result.map((m) => m.id)).toEqual(['orphan']);
  });

  it('keeps existing messages when adding an orphan', () => {
    const result = addMessage([step('a')], step('orphan', 'missing-parent'));

    expect(result.map((m) => m.id)).toEqual(['a', 'orphan']);
  });

  it('upserts by id instead of duplicating (re-emitted ask)', () => {
    const original = step('a');
    const updated = { ...step('a'), output: 'updated' };

    const result = addMessage([original], updated);

    expect(result).toHaveLength(1);
    expect(result[0].output).toBe('updated');
  });

  it('upserts a nested message by id', () => {
    const parent = step('parent');
    let messages = addMessage([parent], step('child', 'parent'));
    messages = addMessage(messages, {
      ...step('child', 'parent'),
      output: 'updated'
    });

    expect(messages).toHaveLength(1);
    expect(messages[0].steps?.[0].output).toBe('updated');
  });
});
