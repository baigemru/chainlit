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

export interface IPendingFirstMessage {
  text: string;
  createdAt: number;
}

// User message to send once the chat started after a server-side
// `set_chat_profile` switch (survives the reconnection remounts).
// createdAt lets a message whose delivery never happened expire instead of
// surfacing in an unrelated conversation later on.
export const pendingFirstMessageState = atom<IPendingFirstMessage | undefined>({
  key: 'PendingFirstMessage',
  default: undefined
});
