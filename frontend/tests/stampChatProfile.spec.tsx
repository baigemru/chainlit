import { describe, expect, it } from 'vitest';

import { IStep, stampChatProfile } from '@chainlit/react-client';

const step = (metadata?: Record<string, any>): IStep =>
  ({
    id: 'm1',
    threadId: '',
    name: 'assistant',
    type: 'assistant_message',
    output: 'hi',
    createdAt: '2026-01-01T00:00:00Z',
    ...(metadata !== undefined ? { metadata } : {})
  }) as IStep;

describe('stampChatProfile', () => {
  it('stamps the active profile into metadata.chat_profile', () => {
    const stamped = stampChatProfile(step(), 'Search');
    expect(stamped.metadata?.chat_profile).toBe('Search');
  });

  it('keeps existing metadata keys', () => {
    const stamped = stampChatProfile(step({ icon: 'bot' }), 'Search');
    expect(stamped.metadata).toEqual({ icon: 'bot', chat_profile: 'Search' });
  });

  it('does not mutate the original message', () => {
    const original = step({ icon: 'bot' });
    stampChatProfile(original, 'Search');
    expect(original.metadata).toEqual({ icon: 'bot' });
  });

  it('never overwrites an existing stamp', () => {
    const original = step({ chat_profile: 'Assistant' });
    const stamped = stampChatProfile(original, 'Search');
    expect(stamped).toBe(original);
    expect(stamped.metadata?.chat_profile).toBe('Assistant');
  });

  it('is a no-op without an active profile', () => {
    const original = step();
    expect(stampChatProfile(original, undefined)).toBe(original);
    expect(stampChatProfile(original, '')).toBe(original);
    expect(original.metadata).toBeUndefined();
  });
});
