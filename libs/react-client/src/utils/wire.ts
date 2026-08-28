/**
 * The seam between the wire structs and the client's own view models.
 *
 * The two are the same objects field for field — the wire is generated from
 * the same shapes the UI has always rendered. They differ only in
 * nullability: msgspec spells "no value" as an explicit `null` on an
 * `Optional` field, while the client's `I*` types spell it as an absent key.
 * These helpers do that one translation, and are the only place a cast
 * between the two families is allowed.
 */
import type {
  Action,
  ProtocolElement,
  Step as WireStep,
  StepPatch as WireStepPatch,
  Thread as WireThread
} from '../protocol';
import type { IAction, IElement, IStep, IThread } from '../types';

/** Shallow copy without the keys whose value is an explicit `null`. */
const withoutNulls = <T extends object>(value: T): T => {
  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (item !== null) out[key] = item;
  }
  return out as T;
};

/**
 * A full step, as stated by `step.upsert`, `step.stream.start` or
 * `ask.start`. These messages state the whole object, so a value at its
 * default is the value — including a `wait` that is simply not there.
 */
export const toStep = (step: WireStep): IStep =>
  withoutNulls(step) as unknown as IStep;

/**
 * A partial step, as stated by `step.update`.
 *
 * Presence is the whole signal here: a field left out of the frame means
 * "no opinion" and must not appear in the returned patch at all, while an
 * explicit `null` means "clear it" and becomes an `undefined` the merge
 * writes over the stored value. A `Step`'s value defaults could not tell
 * those two apart, which is why the wire has a separate patch struct.
 */
export const toStepPatch = (patch: WireStepPatch): Partial<IStep> => {
  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(patch)) {
    if (key === 't') continue;
    out[key] = item === null ? undefined : item;
  }
  return out as Partial<IStep>;
};

/**
 * Pick the fields the wire carries off a client-side step.
 *
 * Spreading the whole `IStep` would put the UI's own additions on the wire —
 * the legacy `indent`, the client-side chat-profile stamp — and `createdAt`
 * may be a number here while the wire is a string.
 */
export const toWireStep = (step: IStep): WireStep => ({
  id: step.id,
  output: step.output,
  name: step.name,
  type: step.type,
  threadId: step.threadId,
  parentId: step.parentId,
  input: step.input,
  createdAt: step.createdAt != null ? String(step.createdAt) : undefined,
  start: step.start != null ? String(step.start) : undefined,
  end: step.end != null ? String(step.end) : undefined,
  isError: step.isError,
  streaming: step.streaming,
  waitForAnswer: step.waitForAnswer,
  showInput: step.showInput,
  defaultOpen: step.defaultOpen,
  autoCollapse: step.autoCollapse,
  language: step.language,
  command: step.command,
  metadata: step.metadata
});

export const toElement = (element: ProtocolElement): IElement =>
  withoutNulls(element) as unknown as IElement;

export const toAction = (action: Action): IAction =>
  withoutNulls(action) as unknown as IAction;

export const toThread = (thread: WireThread): IThread => {
  const steps = (thread.steps ?? []).map(toStep);
  const elements = (thread.elements ?? []).map(toElement);
  return {
    ...(withoutNulls(thread) as unknown as IThread),
    steps,
    elements
  };
};
