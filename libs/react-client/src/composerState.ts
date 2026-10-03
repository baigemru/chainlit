import { atom } from 'recoil';

import type { ComposerState } from './protocol';

/**
 * What the composer says in this conversation: the projection of
 * `composer.state`.
 *
 * Written whole from each frame, never merged: the server sends the full
 * state, and a field the frame leaves out is "as configured" — the
 * translated placeholder, the profile's `composer_hint` — not "as before".
 * `null` and absent are one answer and are both stored as absent, so a
 * reader's `??` falls back on either; `''` is kept, and means "nothing here".
 *
 * Its own file rather than `state.ts`, which another change owns; it is the
 * same kind of atom as the ones there.
 */
export interface IComposerState {
  placeholder?: string;
  hint?: string;
}

export const composerState = atom<IComposerState>({
  key: 'ComposerState',
  default: {}
});

/** A frame as the atom stores it. */
export const composerFromFrame = ({
  placeholder,
  hint
}: Pick<ComposerState, 'placeholder' | 'hint'>): IComposerState => ({
  ...(placeholder != null ? { placeholder } : {}),
  ...(hint != null ? { hint } : {})
});
