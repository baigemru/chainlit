/**
 * The wire protocol, as the client sees it.
 *
 * `messages.ts` next to this file is generated from `chainlit/protocol/` by
 * `backend/scripts/gen_protocol_types.py` and must not be hand-edited. This
 * module re-exports it under names that do not collide with the DOM lib
 * (`Error`), and adds the two enums and the handful of aliases the generator
 * cannot express.
 */
import type {
  AskActionReply,
  AskActionSpec,
  AskElementReply,
  AskElementSpec,
  AskFileReply,
  AskFileSpec,
  AskTextReply,
  AskTextSpec,
  AudioElement,
  CustomElement,
  DataframeElement,
  Error as ErrorMsg,
  FileElement,
  ImageElement,
  PdfElement,
  PlotlyElement,
  ServerMsg,
  TasklistElement,
  TextElement,
  VideoElement
} from './messages';

export * from './messages';

/** `error`, renamed so it does not shadow the global `Error`. */
export type ProtocolError = ErrorMsg;

/** One branch of the `Element` union, discriminated on `type`. */
export type ProtocolElement =
  | ImageElement
  | TextElement
  | PdfElement
  | AudioElement
  | VideoElement
  | FileElement
  | PlotlyElement
  | DataframeElement
  | CustomElement
  | TasklistElement;

/** One branch of the `AskSpec` union, discriminated on `type`. */
export type ProtocolAskSpec =
  | AskTextSpec
  | AskFileSpec
  | AskActionSpec
  | AskElementSpec;

/** One branch of the ask reply union, discriminated on `kind`. */
export type AskReplyValue =
  | AskTextReply
  | AskFileReply
  | AskActionReply
  | AskElementReply;

/** Every tag the server can send, as a value — for runtime checks. */
export type ServerTag = ServerMsg['t'];

/**
 * Websocket close codes in the private-use range, mirroring
 * `chainlit.protocol.codec.CloseCode`.
 */
export const CloseCode = {
  /** The first frame was not a well-formed `hello`. */
  BAD_HANDSHAKE: 4400,
  /** No valid credentials on a server that requires login. */
  UNAUTHENTICATED: 4401,
  /** The session id belongs to another user. */
  SESSION_FORBIDDEN: 4403,
  /** The thread id is not readable by this user. */
  THREAD_FORBIDDEN: 4404,
  /** No `hb.ack` within the deadline. */
  HEARTBEAT_TIMEOUT: 4408,
  /** Another connection took this session over. */
  SUPERSEDED: 4409,
  /** A frame exceeded the transport limit. */
  FRAME_TOO_LARGE: 4413,
  /** Unexpected server-side failure. */
  INTERNAL: 4500
} as const;

export type CloseCodeValue = (typeof CloseCode)[keyof typeof CloseCode];

/**
 * Payload of an `error` message, mirroring `chainlit.protocol.codec.ErrorCode`.
 * Distinct from `CloseCode`: an error leaves the socket open.
 */
export const ErrorCode = {
  BAD_MESSAGE: 'bad_message',
  UNKNOWN_TAG: 'unknown_tag',
  UNAUTHENTICATED: 'unauthenticated',
  UNAUTHORIZED: 'unauthorized',
  SESSION_NOT_FOUND: 'session_not_found',
  THREAD_NOT_FOUND: 'thread_not_found',
  ASK_SLOT_BUSY: 'ask_slot_busy',
  ASK_UNKNOWN: 'ask_unknown',
  PROFILE_FORBIDDEN: 'profile_forbidden',
  RATE_LIMITED: 'rate_limited',
  PAYLOAD_TOO_LARGE: 'payload_too_large',
  INTERNAL: 'internal'
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];
