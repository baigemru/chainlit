import { IElement } from './element';
import { IStep } from './step';

export interface IThread {
  id: string;
  createdAt: number | string;
  name?: string;
  userId?: string;
  userIdentifier?: string;
  /** Thread this one was spawned from by a profile switch, if any. */
  parentThreadId?: string | null;
  metadata?: Record<string, any>;
  steps: IStep[];
  elements?: IElement[];
}
