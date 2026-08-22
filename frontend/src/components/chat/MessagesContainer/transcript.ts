import { IStep } from '@chainlit/react-client';

import { IChatBoundary, IKeptExcursion } from '@/state/chat';

export interface ITranscriptSection {
  key: string;
  messages: IStep[];
  /** Profile the chat below this section's divider started on. */
  startedProfile?: string;
}

/**
 * Clears the `streaming` flag left over on messages kept from a chat whose
 * socket is gone — it would otherwise render a cursor forever and hide the
 * message buttons. New objects are built along the path of every change:
 * mutating in place is allowed by the atom but defeats the memo comparators,
 * so the cursor would never actually disappear.
 */
export const freezeStreaming = (steps: IStep[]): IStep[] => {
  let changed = false;

  const frozen = steps.map((step) => {
    const nested = step.steps ? freezeStreaming(step.steps) : undefined;
    const nestedChanged = !!nested && nested !== step.steps;
    if (!step.streaming && !nestedChanged) return step;
    changed = true;
    return {
      ...step,
      streaming: false,
      ...(nestedChanged ? { steps: nested } : {})
    };
  });

  return changed ? frozen : steps;
};

/**
 * Splits the transcript into the chats it is made of. Boundaries are matched
 * against root messages only — nested steps never carry one, so a run can
 * never be cut in half. Unknown ids are ignored, which is what makes a stale
 * boundary (after a reload or a resume) harmless.
 */
export const splitAtBoundaries = (
  messages: IStep[],
  boundaries: IChatBoundary[]
): ITranscriptSection[] => {
  if (!boundaries.length) {
    return [{ key: 'chat-current', messages }];
  }

  const profileByMessageId = new Map(
    boundaries.map((boundary) => [boundary.afterMessageId, boundary.profile])
  );

  const sections: ITranscriptSection[] = [];
  let current: IStep[] = [];

  for (const message of messages) {
    current.push(message);
    const startedProfile = profileByMessageId.get(message.id);
    if (startedProfile !== undefined) {
      sections.push({
        key: `chat-until-${message.id}`,
        messages: current,
        startedProfile
      });
      current = [];
    }
  }

  // Always present, possibly empty: the divider must show up immediately,
  // before the new chat has produced anything.
  sections.push({ key: 'chat-current', messages: current });

  return sections;
};

export interface ITranscriptViewSection extends ITranscriptSection {
  /**
   * Set on the last section of a kept excursion: the divider drawn after it
   * is the return divider, and the section is the segment its collapse
   * button hides (from the previous divider, or the excursion's start, down
   * to the return line — exactly the child chat's messages).
   */
  excursionId?: string;
  /** True for sections that belong to kept excursions (ended chats). */
  kept: boolean;
}

/**
 * Lays the whole screen out: the excursions kept by returns to a parent
 * thread, oldest first, then the live transcript. Each root message renders
 * once, at its first occurrence — an excursion holds everything that was on
 * screen when the return happened, and the resumed thread's history replays
 * part of it, so later occurrences (in a later excursion or in the live
 * messages) are duplicates by construction. Dividers are matched before the
 * deduplication, so a divider survives even when the messages around it were
 * already shown.
 */
export const buildTranscriptView = (
  excursions: IKeptExcursion[],
  messages: IStep[],
  boundaries: IChatBoundary[]
): ITranscriptViewSection[] => {
  const view: ITranscriptViewSection[] = [];
  const seen = new Set<string>();

  const dedupe = (section: ITranscriptSection): IStep[] => {
    const kept = section.messages.filter((message) => !seen.has(message.id));
    kept.forEach((message) => seen.add(message.id));
    return kept;
  };

  for (const excursion of excursions) {
    const sections = splitAtBoundaries(
      excursion.messages,
      excursion.boundaries
    );
    sections.forEach((section, index) => {
      view.push({
        ...section,
        key: `${excursion.id}-${section.key}`,
        messages: dedupe(section),
        excursionId: index === sections.length - 1 ? excursion.id : undefined,
        kept: true
      });
    });
  }

  for (const section of splitAtBoundaries(messages, boundaries)) {
    view.push({ ...section, messages: dedupe(section), kept: false });
  }

  return view;
};
