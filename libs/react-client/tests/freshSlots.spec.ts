import { describe, expect, it } from 'vitest';

import type { ElementSidebarSeen } from '../src/state';
import type { IElementSidebarState, IMessageElement } from '../src/types';
import { freshSlots, noteSeen } from '../src/useElementSidebar';

/**
 * "This tab changed while you were on another one", which the strip draws as
 * a dot. The rule lives here and nowhere else, and the two cases it most
 * needs to get wrong in the *quiet* direction are the ones a user would
 * notice first: a reload, or a dropped connection, that lights up every tab
 * although nothing in any of them moved.
 */

const element = (id: string, name = id): IMessageElement =>
  ({ id, name, type: 'text', display: 'side' }) as IMessageElement;

const slot = (id: string, elements = [element(`${id}-el`)]) => ({
  id,
  title: id,
  elements,
  closable: true,
  canvas: false
});

const panel = (
  slots = [slot('cards'), slot('report')],
  active: string | null = 'cards'
): IElementSidebarState => ({ slots, active, visible: true, rev: 1 });

const NONE: ElementSidebarSeen = { session: undefined, slots: {} };

/** Run a sequence of frames through the record, as the effect would. */
const replay = (frames: IElementSidebarState[], session = 's1') =>
  frames.reduce((seen, frame) => noteSeen(seen, frame, session), NONE);

describe('fresh slots', () => {
  it('marks an inactive slot whose contents changed, and keeps marking it', () => {
    const refilled = panel([
      slot('cards'),
      slot('report', [element('report-el', 'v2')])
    ]);
    // The refill goes through the record too, as the effect sends it right
    // after the render that drew the dot: a record that took the new
    // contents as seen would put the dot out one frame later.
    const seen = replay([panel(), refilled]);

    expect([...freshSlots(seen, refilled, 's1')]).toEqual(['report']);
  });

  it('clears the mark once the slot is looked at, and keeps it cleared', () => {
    const refilled = panel([
      slot('cards'),
      slot('report', [element('report-el', 'v2')])
    ]);
    const onReport = { ...refilled, active: 'report' };
    const seen = replay([panel(), refilled, onReport]);

    expect(freshSlots(seen, onReport, 's1').size).toBe(0);
    // And back on the first tab, the one just read is not news again.
    expect(freshSlots(seen, refilled, 's1').size).toBe(0);
  });

  it('does not mark a slot whose elements are new objects with the same content', () => {
    // A reconnect replays the panel as fresh upserts: every element object
    // is new, not one of them changed.
    const seen = replay([panel()]);
    const replayed = panel([slot('cards'), slot('report')]);

    expect(freshSlots(seen, replayed, 's1').size).toBe(0);
  });

  it('treats the first panel of a session as a baseline', () => {
    // A reload: the server replays every slot at once, and none of it is
    // news to the person who put it there.
    const first = panel();
    const seen = noteSeen(NONE, first, 's1');

    expect(freshSlots(seen, first, 's1').size).toBe(0);
  });

  it('marks a slot that arrives behind the active tab', () => {
    const grown = panel([slot('cards'), slot('report'), slot('variants')]);
    const seen = replay([panel(), grown]);

    expect([...freshSlots(seen, grown, 's1')]).toEqual(['variants']);
  });

  it('never marks the active slot', () => {
    const seen = replay([panel()]);
    const changed = panel([
      slot('cards', [element('cards-el', 'v2')]),
      slot('report')
    ]);

    expect(freshSlots(seen, changed, 's1').size).toBe(0);
  });

  it('starts over in a new session', () => {
    const seen = replay([panel()], 's1');
    const next = panel([
      slot('cards'),
      slot('report', [element('report-el', 'other chat')])
    ]);

    // Before the record is replaced it describes another panel.
    expect(freshSlots(seen, next, 's2').size).toBe(0);
    const rebased = noteSeen(seen, next, 's2');
    expect(freshSlots(rebased, next, 's2').size).toBe(0);
  });

  it('returns the same record when nothing moved, so its effect settles', () => {
    const seen = replay([panel()]);

    expect(noteSeen(seen, panel(), 's1')).toBe(seen);
  });
});
