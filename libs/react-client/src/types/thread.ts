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

/**
 * One page request against the thread history — the flat body of
 * ``POST /project/threads`` (backend ``ThreadQuery``). ``userId`` is not
 * offered: the backend overwrites it with the caller's own id.
 */
export interface IThreadQuery {
  /** Page size, 1..100; the backend defaults to 20. */
  first?: number;
  /** Opaque ``pageInfo.endCursor`` of the previous page. */
  cursor?: string;
  search?: string;
  feedback?: number;
}
