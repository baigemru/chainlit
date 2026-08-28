import { ChainlitSocket } from '@chainlit/react-client';

export interface IToken {
  id: number | string;
  token: string;
  isSequence: boolean;
}

export interface ISession {
  socket: ChainlitSocket;
  error?: boolean;
}
