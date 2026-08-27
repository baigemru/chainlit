// Generated from backend/chainlit/protocol by scripts/gen_protocol_types.py.
// Do not edit: run `python scripts/gen_protocol_types.py` in backend/ instead.
//
// `ServerMsg` and `ClientMsg` are discriminated on `t`, so a switch over it
// narrows to one branch and a missing case is a compile error.

export interface Action {
  id: string;
  name: string;
  payload?: Record<string, unknown>;
  label?: string;
  tooltip?: string;
  icon?: string | null;
  forId?: string | null;
}

export interface ActionAdd {
  t: 'action.add';
  action: Action;
}

export interface ActionRemove {
  t: 'action.remove';
  id: string;
}

export interface AskActionReply {
  kind: 'action';
  action: Action;
}

export interface AskActionSpec {
  type: 'action';
  stepId: string;
  timeout?: number;
  keys?: string[];
}

export interface AskElementReply {
  kind: 'element';
  submitted?: boolean;
  props?: Record<string, unknown>;
}

export interface AskElementSpec {
  type: 'element';
  stepId: string;
  timeout?: number;
  elementId?: string;
}

export interface AskEnd {
  t: 'ask.end';
  stepId: string;
  reason?: 'answered' | 'cancelled' | 'stale' | 'superseded' | 'timeout';
}

export interface AskFileReply {
  kind: 'file';
  files?: FileRef[];
}

export interface AskFileSpec {
  type: 'file';
  stepId: string;
  timeout?: number;
  accept?: string[] | Record<string, string[]>;
  maxFiles?: number;
  maxSizeMb?: number;
}

export interface AskReply {
  t: 'ask.reply';
  stepId: string;
  value: AskTextReply | AskFileReply | AskActionReply | AskElementReply;
}

export interface AskStart {
  t: 'ask.start';
  spec: AskTextSpec | AskFileSpec | AskActionSpec | AskElementSpec;
  step: Step;
}

export interface AskTextReply {
  kind: 'text';
  step: Step;
}

export interface AskTextSpec {
  type: 'text';
  stepId: string;
  timeout?: number;
}

export interface AudioConnection {
  t: 'audio.connection';
  state: 'off' | 'on';
}

export interface AudioElement {
  type: 'audio';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
  autoPlay?: boolean;
}

export interface AudioEnd {
  t: 'audio.end';
}

export interface AudioIn {
  t: 'audio.in';
  data: string;
  mimeType?: string;
  isStart?: boolean;
  elapsedTime?: number;
}

export interface AudioInterrupt {
  t: 'audio.interrupt';
}

export interface AudioOut {
  t: 'audio.out';
  track: string;
  mimeType: string;
  data: string;
}

export interface AudioStart {
  t: 'audio.start';
}

export interface Command {
  id: string;
  icon?: string;
  description?: string;
  button?: boolean;
  persistent?: boolean;
  selected?: boolean;
}

export interface CommandsSet {
  t: 'commands.set';
  commands?: Command[];
}

export interface CustomElement {
  type: 'custom';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
  props?: Record<string, unknown>;
}

export interface DataframeElement {
  type: 'dataframe';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
}

export interface ElementRemove {
  t: 'element.remove';
  id: string;
}

export interface ElementUpsert {
  t: 'element.upsert';
  element:
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
}

export interface Error {
  t: 'error';
  code: string;
  message?: string;
  detail?: Record<string, unknown> | null;
  fatal?: boolean;
}

export interface FavoritesFetch {
  t: 'favorites.fetch';
}

export interface FavoritesSet {
  t: 'favorites.set';
  steps?: Step[];
}

export interface Feedback {
  value: 0 | 1;
  forId: string;
  id?: string | null;
  threadId?: string | null;
  comment?: string | null;
}

export interface FileElement {
  type: 'file';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
}

export interface FileRef {
  id: string;
}

export interface Heartbeat {
  t: 'hb';
  seq?: number;
}

export interface HeartbeatAck {
  t: 'hb.ack';
  seq?: number;
}

export interface Hello {
  t: 'hello';
  sessionId: string;
  clientType?: 'copilot' | 'discord' | 'slack' | 'teams' | 'webapp';
  threadId?: string | null;
  chatProfile?: string | null;
  userEnv?: Record<string, string>;
  pageLoad?: boolean;
  protocolVersion?: number;
}

export interface ImageElement {
  type: 'image';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
  size?: 'large' | 'medium' | 'small' | null;
}

export interface InputWidgetSpec {
  id: string;
  label: string;
  type?:
    | 'checkbox'
    | 'datepicker'
    | 'multiselect'
    | 'numberinput'
    | 'radio'
    | 'select'
    | 'slider'
    | 'switch'
    | 'tags'
    | 'textinput';
  initial?: unknown;
  tooltip?: string | null;
  description?: string | null;
  disabled?: boolean;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  items?: unknown;
  placeholder?: string | null;
  multiline?: boolean;
  mode?: string | null;
  format?: string | null;
  minDate?: string | null;
  maxDate?: string | null;
  inputs?: unknown;
}

export interface MessageEdit {
  t: 'message.edit';
  message: Step;
}

export interface MessageFavorite {
  t: 'message.favorite';
  messageId: string;
  favorite?: boolean;
}

export interface MessageSend {
  t: 'message.send';
  message: Step;
  fileReferences?: FileRef[];
}

export interface Mode {
  id: string;
  name: string;
  options?: ModeOption[];
}

export interface ModeOption {
  id: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  default?: boolean;
}

export interface ModesSet {
  t: 'modes.set';
  modes?: Mode[];
}

export interface PdfElement {
  type: 'pdf';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
  page?: number | null;
}

export interface PlotlyElement {
  type: 'plotly';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
}

export interface ProfileChanged {
  t: 'profile.changed';
  chatProfile: string;
  previous?: string | null;
  sync?: boolean;
}

export interface ProfileSwitch {
  t: 'profile.switch';
  chatProfile: string;
}

export interface Reload {
  t: 'reload';
}

export interface RpcCall {
  t: 'rpc.call';
  callId: string;
  name: string;
  args?: Record<string, unknown>;
}

export interface RpcCancel {
  t: 'rpc.cancel';
  callId: string;
  reason?: 'answered' | 'cancelled' | 'timeout';
}

export interface RpcResult {
  t: 'rpc.result';
  callId: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
}

export interface SessionClear {
  t: 'session.clear';
}

export interface SessionHandoff {
  t: 'session.handoff';
  chatProfile: string;
  nextSessionId?: string | null;
  keepTranscript?: boolean;
  hasTransitMessage?: boolean;
}

export interface SessionReady {
  t: 'session.ready';
  sessionId: string;
  threadId?: string | null;
  chatProfile?: string | null;
  restored?: boolean;
  heartbeatIntervalMs?: number;
}

export interface SettingsChange {
  t: 'settings.change';
  settings?: Record<string, unknown>;
}

export interface SettingsEdit {
  t: 'settings.edit';
  settings?: Record<string, unknown>;
}

export interface SettingsSet {
  t: 'settings.set';
  inputs?: InputWidgetSpec[];
}

export interface SidebarSet {
  t: 'sidebar.set';
  title?: string | null;
  elements?:
    | ImageElement
    | TextElement
    | PdfElement
    | AudioElement
    | VideoElement
    | FileElement
    | PlotlyElement
    | DataframeElement
    | CustomElement
    | TasklistElement[];
  key?: string | null;
}

export interface Step {
  id: string;
  output?: string;
  name?: string;
  type?:
    | 'assistant_message'
    | 'embedding'
    | 'llm'
    | 'rerank'
    | 'retrieval'
    | 'run'
    | 'system_message'
    | 'tool'
    | 'undefined'
    | 'user_message';
  threadId?: string | null;
  parentId?: string | null;
  input?: string;
  createdAt?: string | null;
  start?: string | null;
  end?: string | null;
  isError?: boolean;
  streaming?: boolean;
  waitForAnswer?: boolean;
  showInput?: boolean | string;
  defaultOpen?: boolean;
  autoCollapse?: boolean;
  language?: string | null;
  icon?: string | null;
  command?: string | null;
  modes?: Record<string, string> | null;
  tags?: string[] | null;
  metadata?: Record<string, unknown> | null;
  generation?: Record<string, unknown> | null;
  feedback?: null | Feedback;
  wait?: null | Wait;
  steps?: Step[] | null;
}

export interface StepDelete {
  t: 'step.delete';
  stepId: string;
}

export interface StepPatch {
  id: string;
  output?: string;
  name?: string;
  type?:
    | 'assistant_message'
    | 'embedding'
    | 'llm'
    | 'rerank'
    | 'retrieval'
    | 'run'
    | 'system_message'
    | 'tool'
    | 'undefined'
    | 'user_message';
  threadId?: string | null;
  parentId?: string | null;
  input?: string;
  createdAt?: string | null;
  start?: string | null;
  end?: string | null;
  isError?: boolean;
  streaming?: boolean;
  waitForAnswer?: boolean;
  showInput?: boolean | string;
  defaultOpen?: boolean;
  autoCollapse?: boolean;
  language?: string | null;
  icon?: string | null;
  command?: string | null;
  modes?: Record<string, string> | null;
  tags?: string[] | null;
  metadata?: Record<string, unknown> | null;
  generation?: Record<string, unknown> | null;
  feedback?: null | Feedback;
  wait?: null | Wait;
}

export interface StepStreamStart {
  t: 'step.stream.start';
  step: Step;
}

export interface StepStreamToken {
  t: 'step.stream.token';
  id: string;
  token: string;
  isSequence?: boolean;
  isInput?: boolean;
}

export interface StepUpdate {
  t: 'step.update';
  step: StepPatch;
}

export interface StepUpsert {
  t: 'step.upsert';
  step: Step;
}

export interface Stop {
  t: 'stop';
}

export interface TaskIndicator {
  t: 'task.indicator';
  running: boolean;
}

export interface TasklistElement {
  type: 'tasklist';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
}

export interface TextElement {
  type: 'text';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
  language?: string | null;
}

export interface Thread {
  id: string;
  createdAt?: string;
  name?: string | null;
  userId?: string | null;
  userIdentifier?: string | null;
  parentThreadId?: string | null;
  tags?: string[] | null;
  metadata?: Record<string, unknown> | null;
  steps?: Step[];
  elements?:
    | ImageElement
    | TextElement
    | PdfElement
    | AudioElement
    | VideoElement
    | FileElement
    | PlotlyElement
    | DataframeElement
    | CustomElement
    | TasklistElement[];
}

export interface ThreadFirstInteraction {
  t: 'thread.first_interaction';
  interaction: string;
  threadId: string;
}

export interface ThreadOpen {
  t: 'thread.open';
  threadId: string;
  keepTranscript?: boolean;
}

export interface ThreadParent {
  t: 'thread.parent';
  parentThreadId: string;
}

export interface ThreadResume {
  t: 'thread.resume';
  thread: Thread;
}

export interface ThreadResumeError {
  t: 'thread.resume_error';
  error: string;
}

export interface Toast {
  t: 'toast';
  message: string;
  type?: 'error' | 'info' | 'success' | 'warning';
}

export interface TokenUsage {
  t: 'token.usage';
  count: number;
}

export interface VideoElement {
  type: 'video';
  id: string;
  name?: string;
  display?: 'inline' | 'page' | 'side';
  threadId?: string | null;
  forId?: string | null;
  url?: string | null;
  chainlitKey?: string | null;
  objectKey?: string | null;
  path?: string | null;
  mime?: string | null;
  size?: 'large' | 'medium' | 'small' | null;
  playerConfig?: Record<string, unknown> | null;
}

export interface Wait {
  texts?: string[];
  intervalMs?: number;
  loop?: boolean;
}

export interface ClientWindowMessage {
  t: 'window.message';
  data?: unknown;
}

export interface ServerWindowMessage {
  t: 'window.message';
  data?: unknown;
}

export type ServerMsg =
  | SessionReady
  | Error
  | Heartbeat
  | Reload
  | StepUpsert
  | StepUpdate
  | StepDelete
  | StepStreamStart
  | StepStreamToken
  | ElementUpsert
  | ElementRemove
  | ActionAdd
  | ActionRemove
  | AskStart
  | AskEnd
  | TaskIndicator
  | ThreadResume
  | ThreadResumeError
  | ThreadFirstInteraction
  | ThreadParent
  | ThreadOpen
  | ProfileChanged
  | SessionHandoff
  | SettingsSet
  | CommandsSet
  | ModesSet
  | FavoritesSet
  | SidebarSet
  | AudioConnection
  | AudioOut
  | AudioInterrupt
  | RpcCall
  | RpcCancel
  | Toast
  | TokenUsage
  | ServerWindowMessage;

export type ServerMsgTag =
  | 'session.ready'
  | 'error'
  | 'hb'
  | 'reload'
  | 'step.upsert'
  | 'step.update'
  | 'step.delete'
  | 'step.stream.start'
  | 'step.stream.token'
  | 'element.upsert'
  | 'element.remove'
  | 'action.add'
  | 'action.remove'
  | 'ask.start'
  | 'ask.end'
  | 'task.indicator'
  | 'thread.resume'
  | 'thread.resume_error'
  | 'thread.first_interaction'
  | 'thread.parent'
  | 'thread.open'
  | 'profile.changed'
  | 'session.handoff'
  | 'settings.set'
  | 'commands.set'
  | 'modes.set'
  | 'favorites.set'
  | 'sidebar.set'
  | 'audio.connection'
  | 'audio.out'
  | 'audio.interrupt'
  | 'rpc.call'
  | 'rpc.cancel'
  | 'toast'
  | 'token.usage'
  | 'window.message';

/** Exhaustive handler table: omitting a message is a compile error. */
export type ServerMsgHandlers = {
  [K in ServerMsg['t']]: (message: Extract<ServerMsg, { t: K }>) => void;
};

export type ClientMsg =
  | Hello
  | HeartbeatAck
  | SessionClear
  | Stop
  | MessageSend
  | MessageEdit
  | MessageFavorite
  | FavoritesFetch
  | AskReply
  | ProfileSwitch
  | SettingsChange
  | SettingsEdit
  | AudioStart
  | AudioIn
  | AudioEnd
  | RpcResult
  | ClientWindowMessage;

export type ClientMsgTag =
  | 'hello'
  | 'hb.ack'
  | 'session.clear'
  | 'stop'
  | 'message.send'
  | 'message.edit'
  | 'message.favorite'
  | 'favorites.fetch'
  | 'ask.reply'
  | 'profile.switch'
  | 'settings.change'
  | 'settings.edit'
  | 'audio.start'
  | 'audio.in'
  | 'audio.end'
  | 'rpc.result'
  | 'window.message';

/** Exhaustive handler table: omitting a message is a compile error. */
export type ClientMsgHandlers = {
  [K in ClientMsg['t']]: (message: Extract<ClientMsg, { t: K }>) => void;
};
