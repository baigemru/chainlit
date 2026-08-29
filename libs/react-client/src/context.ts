import { createContext, useContext } from 'react';

import { ChainlitAPI } from './api';
import { ChatTransport, chatTransportFor } from './transport';

const defaultChainlitContext = undefined;

const ChainlitContext = createContext<ChainlitAPI>(
  new ChainlitAPI('http://localhost:8000', 'webapp')
);

/**
 * An explicit transport for this tree. Left empty by the app, which gets the
 * one that belongs to its API client; a test or an embedder that wants to
 * drive the connection itself provides one here.
 */
const ChatTransportContext = createContext<ChatTransport | undefined>(
  undefined
);

/**
 * The transport speaking for this tree's server.
 *
 * Deliberately not a Recoil atom: the transport is a live object with a
 * socket in it, and putting it in the store is what made `close()` write the
 * atom from inside an atom updater.
 */
const useChatTransport = (): ChatTransport => {
  const client = useContext(ChainlitContext);
  const provided = useContext(ChatTransportContext);
  return provided ?? chatTransportFor(client);
};

export {
  ChainlitContext,
  ChatTransportContext,
  defaultChainlitContext,
  useChatTransport
};
