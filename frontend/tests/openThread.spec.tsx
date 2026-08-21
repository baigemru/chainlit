import { isThreadAvailable, shouldRetireTransition } from '@/lib/openThread';
import { afterEach, describe, expect, it, vi } from 'vitest';

describe('isThreadAvailable', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('is true for an ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200 })
    );
    await expect(isThreadAvailable('/project/thread/t1')).resolves.toBe(true);
  });

  it('handles a 401 locally instead of surfacing it', async () => {
    // A foreign thread answers 401; going through the shared api client
    // would trip the global on401 handler and hard-redirect to /login.
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    vi.stubGlobal('fetch', fetchMock);
    await expect(isThreadAvailable('/project/thread/t1')).resolves.toBe(false);
    expect(fetchMock).toHaveBeenCalledWith('/project/thread/t1', {
      credentials: 'include'
    });
  });

  it('is false for a missing thread', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404 })
    );
    await expect(isThreadAvailable('/project/thread/t1')).resolves.toBe(false);
  });

  it('is false when the request itself fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(isThreadAvailable('/project/thread/t1')).resolves.toBe(false);
  });
});

describe('shouldRetireTransition', () => {
  const base = {
    transition: { threadId: 'parent', keepTranscript: true },
    currentThreadId: undefined,
    pathname: '/thread/parent',
    resumeError: false,
    sessionError: false
  };

  it('does nothing without a transition', () => {
    expect(shouldRetireTransition({ ...base, transition: undefined })).toBe(
      false
    );
  });

  it('keeps the normal in-flight state', () => {
    // Right after the click the chat still points at the old thread (or at
    // nothing once the teardown ran) while the pathname is already the
    // target: not abandonment.
    expect(shouldRetireTransition(base)).toBe(false);
    expect(shouldRetireTransition({ ...base, currentThreadId: 'child' })).toBe(
      false
    );
  });

  it('retires on success', () => {
    expect(shouldRetireTransition({ ...base, currentThreadId: 'parent' })).toBe(
      true
    );
  });

  it('retires on resume or session errors', () => {
    expect(shouldRetireTransition({ ...base, resumeError: true })).toBe(true);
    expect(shouldRetireTransition({ ...base, sessionError: true })).toBe(true);
  });

  it('retires when the navigation is abandoned', () => {
    // Browser Back to the child thread
    expect(shouldRetireTransition({ ...base, pathname: '/thread/child' })).toBe(
      true
    );
    // New chat
    expect(shouldRetireTransition({ ...base, pathname: '/' })).toBe(true);
  });
});
