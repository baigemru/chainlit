import { atom } from 'recoil';

import { ICommand } from 'client-types/*';

export interface IAttachment {
  id: string;
  serverId?: string;
  name: string;
  size: number;
  type: string;
  uploadProgress?: number;
  uploaded?: boolean;
  cancel?: () => void;
  remove?: () => void;
  file?: File;
}

export const attachmentsState = atom<IAttachment[]>({
  key: 'Attachments',
  default: []
});

export const persistentCommandState = atom<ICommand | undefined>({
  key: 'PersistentCommand',
  default: undefined
});

export interface IChatBoundary {
  /** Id of the last root message of the chat that ended here. */
  afterMessageId: string;
  /** Profile the new chat started on, used to label the divider. */
  profile: string;
}

// Where a `set_chat_profile(keep_transcript=True)` switch ended one chat and
// started the next, so the transcript can be split with a divider. Client-side
// only: a reload or a thread opened from the history drops these, and an id
// matching no message simply draws nothing.
export const chatBoundariesState = atom<IChatBoundary[]>({
  key: 'ChatBoundaries',
  default: []
});
