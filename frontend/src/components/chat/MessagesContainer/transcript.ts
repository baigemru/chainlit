import { IStep } from '@chainlit/react-client';

import { IChatBoundary } from '@/state/chat';

export interface ITranscriptSection {
  key: string;
  messages: IStep[];
  /** Profile the chat below this section's divider started on. */
  startedProfile?: string;
}

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
