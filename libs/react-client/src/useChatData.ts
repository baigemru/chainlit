import { useSyncExternalStore } from 'react';
import { useRecoilValue } from 'recoil';

import { useChatTransport } from './context';
import {
  actionState,
  askUserState,
  elementState,
  loadingState,
  tasklistState
} from './state';

export interface IToken {
  id: number | string;
  token: string;
  isSequence: boolean;
  isInput: boolean;
}

const useChatData = () => {
  const loading = useRecoilValue(loadingState);
  const elements = useRecoilValue(elementState);
  const tasklists = useRecoilValue(tasklistState);
  const actions = useRecoilValue(actionState);
  const askUser = useRecoilValue(askUserState);

  // Read straight from the transport rather than from a mirror in the store:
  // the connection is a live object, and every copy of its state that lived
  // in an atom had to be kept in step with it by hand.
  const transport = useChatTransport();
  const { connected, error, superseded } = useSyncExternalStore(
    transport.subscribe,
    transport.getSnapshot,
    transport.getSnapshot
  );

  const disabled =
    !connected ||
    loading ||
    askUser?.spec.type === 'file' ||
    askUser?.spec.type === 'action' ||
    askUser?.spec.type === 'element' ||
    // A reply is already in flight: lock the composer so typed text is not
    // silently swallowed by the replyMessage guard.
    !!askUser?.awaitingReply;

  return {
    actions,
    askUser,
    connected,
    disabled,
    elements,
    error,
    loading,
    // Close 4409: this conversation is being had in another connection.
    // Distinct from `error`, which means something went wrong.
    superseded,
    tasklists
  };
};

export { useChatData };
