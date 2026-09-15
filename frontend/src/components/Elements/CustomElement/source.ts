/**
 * One request for `public/elements/<name>.jsx` per element name per page.
 *
 * The source is a static asset shared by every instance of a name, but the
 * component that wants it is per instance: a message carrying twelve cards of
 * one name used to issue twelve requests, and one more every time anything in
 * the chat remounted them.
 *
 * A *failure* is deliberately not remembered. `ChainlitAPI.get` rejects on any
 * non-2xx as well as on a network error, so a cached rejection would turn a 401
 * during a token refresh, or one offline blip, into a permanent Alert on every
 * card of that name until the page is reloaded. The in-flight promise is still
 * shared, so cards mounted together still make one request between them; the
 * next mount after a failure tries again.
 */
import type { ChainlitAPI } from '@chainlit/react-client';

export type ElementSource = {
  promise: Promise<string>;
  /** Set once resolved, so a later instance renders without a blank frame. */
  code?: string;
};

const sources = new Map<string, ElementSource>();

export const getElementSource = (
  apiClient: ChainlitAPI,
  name: string
): ElementSource => {
  const cached = sources.get(name);
  if (cached) return cached;

  const entry: ElementSource = {
    promise: apiClient
      .get(`/public/elements/${name}.jsx`)
      .then((res) => res.text())
  };
  // A second chain, so the rejection is handled even before a caller attaches.
  entry.promise.then(
    (code) => {
      entry.code = code;
    },
    () => {
      // Unless someone has already started a fresh attempt for this name.
      if (sources.get(name) === entry) sources.delete(name);
    }
  );
  sources.set(name, entry);
  return entry;
};

/** The cache outlives a component on purpose; it must not outlive a test. */
export const resetElementSourceCache = () => sources.clear();
