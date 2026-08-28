import { IAction } from './action';
import { IStep } from './step';

/**
 * Answer to a custom-element ask. The element's own props are nested rather
 * than spread over the top level, where a prop named `type` or `id` could
 * shadow a protocol field.
 */
export interface IAskElementResponse {
  submitted: boolean;
  props?: Record<string, unknown>;
}

export interface FileSpec {
  accept?: string[] | Record<string, string[]>;
  maxSizeMb?: number;
  maxFiles?: number;
}

export interface ActionSpec {
  keys?: string[];
}

export interface IFileRef {
  id: string;
}

export interface IAsk {
  callback: (
    payload: IStep | IFileRef[] | IAction | IAskElementResponse
  ) => void;
  spec: {
    type: 'text' | 'file' | 'action' | 'element';
    stepId: string;
    timeout: number;
    elementId?: string;
  } & FileSpec &
    ActionSpec;
  parentId?: string;
  // Set once a reply has been sent, so forms can lock themselves against
  // double submission. A re-emitted ask (reconnect) resets it.
  awaitingReply?: boolean;
}
