import { describe, expect, it } from 'vitest';

// Through the package entry, not `src/api` directly: `src/api/index.tsx`
// re-exports the auth hooks, which reach back to `src/context.ts` for the
// default client — importing the module directly enters that cycle at the
// wrong end and the class is not defined yet.
import { ChainlitAPI } from '../src/index';

/**
 * `resolveElementUrl` is the only thing standing between a persisted element
 * and a broken `<img>`: the backend now hands out app-relative element urls,
 * and the page origin alone resolves them wrongly under a `root_path` prefix
 * and for an embedder whose chainlit server is a different origin entirely.
 *
 * The cases below are the four shapes a stored url can have — relative,
 * absolute, protocol-relative, absent — plus the query-param join, because
 * such an embedder carries `additionalQueryParams` on every request.
 */
const api = (
  endpoint = 'https://app.example.com/chat/',
  params?: Record<string, string>
) => new ChainlitAPI(endpoint, 'webapp', params);

describe('resolveElementUrl', () => {
  it('resolves an app-relative url against the endpoint, prefix included', () => {
    expect(api().resolveElementUrl('/project/thread/t1/element/e1/file')).toBe(
      'https://app.example.com/chat/project/thread/t1/element/e1/file'
    );
  });

  it('resolves against a different origin', () => {
    expect(
      api('https://chainlit.example.com').resolveElementUrl('/project/file/x')
    ).toBe('https://chainlit.example.com/project/file/x');
  });

  it('leaves an absolute external url alone', () => {
    // `cl.Image(url="https://...")` never had a file on this server.
    const external = 'https://cdn.example.org/cat.png?size=2';
    expect(api().resolveElementUrl(external)).toBe(external);
  });

  it('leaves a protocol-relative url alone', () => {
    expect(api().resolveElementUrl('//cdn.example.org/cat.png')).toBe(
      '//cdn.example.org/cat.png'
    );
  });

  it('leaves a data url alone', () => {
    expect(api().resolveElementUrl('data:image/png;base64,AAAA')).toBe(
      'data:image/png;base64,AAAA'
    );
  });

  it('keeps an absent url absent', () => {
    expect(api().resolveElementUrl(undefined)).toBeUndefined();
    expect(api().resolveElementUrl('')).toBe('');
  });

  it('appends additionalQueryParams the way every other endpoint gets them', () => {
    expect(
      api('https://app.example.com/', { key: 'abc' }).resolveElementUrl(
        '/project/thread/t1/element/e1/file'
      )
    ).toBe('https://app.example.com/project/thread/t1/element/e1/file?key=abc');
  });

  it('joins a url that already carries a query with & and not ?', () => {
    expect(
      api('https://app.example.com/', { key: 'abc' }).resolveElementUrl(
        '/project/file/e1?session_id=s1'
      )
    ).toBe('https://app.example.com/project/file/e1?session_id=s1&key=abc');
  });

  it('is idempotent, so overlapping ingress points may both apply it', () => {
    const once = api().resolveElementUrl('/project/file/e1');
    expect(api().resolveElementUrl(once)).toBe(once);
  });
});
