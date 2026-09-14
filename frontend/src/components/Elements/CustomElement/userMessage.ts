/**
 * The step a custom element sends on the user's behalf.
 *
 * Lives outside the component because the one decision it holds -- what an
 * element's optional payload does to the message's metadata -- is worth a
 * test, and observing it through `CustomElement` would mean standing up a
 * Recoil root, a transport and an in-browser JSX compiler to look at one
 * object.
 */
import { v4 as uuidv4 } from 'uuid';

import type { IStep } from '@chainlit/react-client';

/**
 * What an element wants `on_message` to read instead of the prose.
 *
 * JSON only: it travels inside the step as a wire frame and is written to
 * the JSONB `steps.metadata` column, and neither hop knows what to do with
 * a class instance, a `Date` or an `undefined`.
 */
export type UserMessagePayload = Record<string, unknown>;

export const buildUserMessage = (
  message: string,
  author: string,
  payload?: UserMessagePayload,
  command?: string
): IStep => ({
  threadId: '',
  id: uuidv4(),
  name: author,
  type: 'user_message',
  output: message,
  createdAt: new Date().toISOString(),
  // Nested under its own key, never spread: `location` is the client's
  // stamp and an app key of the same name must not be able to overwrite
  // it. An absent payload writes no key at all, so an element that passes
  // none sends what it sent before the argument existed.
  metadata: {
    location: window.location.href,
    ...(payload !== undefined ? { payload } : {})
  },
  command
});
