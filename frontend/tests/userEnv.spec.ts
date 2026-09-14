import { describe, expect, it } from 'vitest';

import { requiredEnvPresent } from '../src/lib/userEnv';

describe('requiredEnvPresent', () => {
  it('is satisfied by an app that requires nothing', () => {
    expect(requiredEnvPresent(undefined, {})).toBe(true);
    expect(requiredEnvPresent([], {})).toBe(true);
  });

  it('waits for every required key', () => {
    expect(requiredEnvPresent(['A', 'B'], { A: 'x' })).toBe(false);
    expect(requiredEnvPresent(['A', 'B'], { A: 'x', B: 'y' })).toBe(true);
  });

  it('treats an empty value as missing', () => {
    expect(requiredEnvPresent(['A'], { A: '' })).toBe(false);
  });
});
