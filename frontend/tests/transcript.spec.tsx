import { describe, expect, it } from 'vitest';

import { IStep } from '@chainlit/react-client';

import {
  buildTranscriptView,
  freezeStreaming,
  splitAtBoundaries
} from '@/components/chat/MessagesContainer/transcript';

import { IKeptExcursion } from '@/state/chat';

const step = (id: string, extra: Partial<IStep> = {}): IStep =>
  ({
    id,
    threadId: '',
    name: 'assistant',
    type: 'assistant_message',
    output: id,
    createdAt: '2026-01-01T00:00:00Z',
    ...extra
  }) as IStep;

const ids = (steps: IStep[]) => steps.map((s) => s.id);

describe('splitAtBoundaries', () => {
  it('returns a single current section without boundaries', () => {
    const messages = [step('a'), step('b')];
    const sections = splitAtBoundaries(messages, []);
    expect(sections).toHaveLength(1);
    expect(sections[0].key).toBe('chat-current');
    expect(ids(sections[0].messages)).toEqual(['a', 'b']);
    expect(sections[0].startedProfile).toBeUndefined();
  });

  it('cuts after the boundary message and keeps a trailing section', () => {
    const messages = [step('a'), step('b'), step('c')];
    const sections = splitAtBoundaries(messages, [
      { afterMessageId: 'b', profile: 'Search' }
    ]);
    expect(sections).toHaveLength(2);
    expect(ids(sections[0].messages)).toEqual(['a', 'b']);
    expect(sections[0].startedProfile).toBe('Search');
    expect(ids(sections[1].messages)).toEqual(['c']);
  });

  it('ignores boundaries pointing at unknown messages', () => {
    const messages = [step('a')];
    const sections = splitAtBoundaries(messages, [
      { afterMessageId: 'gone', profile: 'Search' }
    ]);
    expect(sections).toHaveLength(1);
    expect(ids(sections[0].messages)).toEqual(['a']);
  });
});

describe('buildTranscriptView', () => {
  const excursion = (
    id: string,
    messages: IStep[],
    boundaries: IKeptExcursion['boundaries'] = []
  ): IKeptExcursion => ({ id, messages, boundaries });

  it('is the plain split when there are no excursions', () => {
    const view = buildTranscriptView([], [step('a')], []);
    expect(view).toHaveLength(1);
    expect(view[0].kept).toBe(false);
    expect(view[0].excursionId).toBeUndefined();
    expect(ids(view[0].messages)).toEqual(['a']);
  });

  it('marks only the last section of an excursion as its segment', () => {
    // Parent messages p1..p2, a switch divider, then the child chat c1.
    const view = buildTranscriptView(
      [
        excursion(
          'x1',
          [step('p1'), step('p2'), step('c1')],
          [{ afterMessageId: 'p2', profile: 'Search' }]
        )
      ],
      [],
      []
    );

    expect(view).toHaveLength(3);
    expect(view[0]).toMatchObject({ kept: true, startedProfile: 'Search' });
    expect(view[0].excursionId).toBeUndefined();
    expect(ids(view[0].messages)).toEqual(['p1', 'p2']);
    // The collapsible segment: exactly the child chat's messages.
    expect(view[1]).toMatchObject({ kept: true, excursionId: 'x1' });
    expect(ids(view[1].messages)).toEqual(['c1']);
    // The live chat is always present, even when still empty.
    expect(view[2]).toMatchObject({ kept: false });
    expect(ids(view[2].messages)).toEqual([]);
  });

  it('never renders a root message twice', () => {
    // The resumed parent history replays p1/p2, which are already on screen
    // inside the excursion; only the genuinely new p3 may render below.
    const view = buildTranscriptView(
      [
        excursion(
          'x1',
          [step('p1'), step('p2'), step('c1')],
          [{ afterMessageId: 'p2', profile: 'Search' }]
        )
      ],
      [step('p1'), step('p2'), step('p3')],
      []
    );

    const live = view[view.length - 1];
    expect(ids(live.messages)).toEqual(['p3']);
  });

  it('dedupes across excursions, first occurrence wins', () => {
    // Second trip: the screen captured on the second return contains the
    // parent history again (replayed by the first resume) plus the second
    // child chat.
    const first = excursion(
      'x1',
      [step('p1'), step('c1')],
      [{ afterMessageId: 'p1', profile: 'Search' }]
    );
    const second = excursion(
      'x2',
      [step('p1'), step('c1'), step('p2'), step('c2')],
      [{ afterMessageId: 'p2', profile: 'Search' }]
    );

    const view = buildTranscriptView([first, second], [step('p1')], []);

    expect(view.map((s) => ids(s.messages))).toEqual([
      ['p1'], // x1: parent before the switch
      ['c1'], // x1: child chat, collapsible
      ['p2'], // x2: p1/c1 already shown above
      ['c2'], // x2: child chat, collapsible
      [] // live
    ]);
    expect(view[1].excursionId).toBe('x1');
    expect(view[3].excursionId).toBe('x2');
    expect(view[0].excursionId).toBeUndefined();
    expect(view[2].excursionId).toBeUndefined();
  });

  it('keeps a divider whose messages were all shown before', () => {
    // Everything in the excursion's first section is a duplicate, but the
    // switch divider between the sections must survive.
    const view = buildTranscriptView(
      [
        excursion('x1', [step('p1')], []),
        excursion(
          'x2',
          [step('p1'), step('c1')],
          [{ afterMessageId: 'p1', profile: 'Search' }]
        )
      ],
      [],
      []
    );

    expect(view.map((s) => ids(s.messages))).toEqual([
      ['p1'],
      [], // x2's pre-switch section: fully deduped...
      ['c1'],
      []
    ]);
    // ...but its divider label is still there.
    expect(view[1].startedProfile).toBe('Search');
  });

  it('splits the live messages at live boundaries below the excursions', () => {
    const view = buildTranscriptView(
      [excursion('x1', [step('c1')], [])],
      [step('a'), step('b')],
      [{ afterMessageId: 'a', profile: 'Search' }]
    );

    expect(view.map((s) => ids(s.messages))).toEqual([['c1'], ['a'], ['b']]);
    expect(view[0].excursionId).toBe('x1');
    expect(view[1]).toMatchObject({ kept: false, startedProfile: 'Search' });
    expect(view[2]).toMatchObject({ kept: false });
  });
});

describe('freezeStreaming', () => {
  it('clears streaming flags, nested steps included', () => {
    const frozen = freezeStreaming([
      step('a', { streaming: true }),
      step('b', { steps: [step('b1', { streaming: true })] })
    ]);
    expect(frozen[0].streaming).toBe(false);
    expect(frozen[1].steps?.[0].streaming).toBe(false);
  });

  it('returns the same reference when nothing streams', () => {
    const steps = [step('a'), step('b', { steps: [step('b1')] })];
    expect(freezeStreaming(steps)).toBe(steps);
  });
});
