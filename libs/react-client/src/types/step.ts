import { IFeedback } from './feedback';

type StepType =
  | 'assistant_message'
  | 'user_message'
  | 'system_message'
  | 'run'
  | 'tool'
  | 'llm'
  | 'embedding'
  | 'retrieval'
  | 'rerank'
  | 'undefined';

/**
 * Transient client-side "waiting" presentation for a message: shimmer plus a
 * rotation of status texts. Sent on `new_message`/`update_message` payloads
 * only — never persisted, so history renders the step statically.
 */
export interface IStepWait {
  /** Rotation texts; empty/absent means shimmer only over the output. */
  texts?: string[];
  /** Interval between text switches (default 5000, min 2000). */
  intervalMs?: number;
  /** Cycle through the texts instead of holding on the last one. */
  loop?: boolean;
}

export interface IStep {
  id: string;
  name: string;
  type: StepType;
  threadId?: string;
  parentId?: string;
  isError?: boolean;
  command?: string;
  modes?: Record<string, string>;
  showInput?: boolean | string;
  waitForAnswer?: boolean;
  input?: string;
  output: string;
  createdAt: number | string;
  start?: number | string;
  end?: number | string;
  feedback?: IFeedback;
  language?: string;
  defaultOpen?: boolean;
  autoCollapse?: boolean;
  streaming?: boolean;
  wait?: IStepWait;
  steps?: IStep[];
  metadata?: Record<string, any>;
  //legacy
  indent?: number;
}
