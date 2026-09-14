import { describe, expect, it } from 'vitest';

// A relative path, not the `@/` alias: `tests/tsconfig.json` includes only
// `**/*.spec.tsx`, so `vite-tsconfig-paths` maps nothing for a `.spec.ts`.
import { buildUserMessage } from '../src/components/Elements/CustomElement/userMessage';

describe('buildUserMessage', () => {
  it('carries the payload under metadata.payload', () => {
    const step = buildUserMessage('Разбери выдачу', 'alice', {
      intent: 'analyze_results',
      shortlistId: 7
    });

    expect(step.metadata?.payload).toEqual({
      intent: 'analyze_results',
      shortlistId: 7
    });
    expect(step.metadata?.location).toBe(window.location.href);
  });

  it('writes no payload key when the element passes none', () => {
    const step = buildUserMessage('плитка', 'alice');

    // The whole promise of the second argument being optional: an element
    // that ignores it sends the same object it sent before it existed.
    expect(step.metadata).toEqual({ location: window.location.href });
    expect('payload' in (step.metadata ?? {})).toBe(false);
  });

  it('keeps the command out of the metadata', () => {
    const step = buildUserMessage('плитка', 'alice', { a: 1 }, 'search');

    expect(step.command).toBe('search');
    expect(step.metadata).toEqual({
      location: window.location.href,
      payload: { a: 1 }
    });
  });

  it('states the author and the user_message type', () => {
    const step = buildUserMessage('hi', 'alice');

    expect(step.name).toBe('alice');
    expect(step.type).toBe('user_message');
    expect(step.output).toBe('hi');
    expect(step.id).toBeTruthy();
  });
});
