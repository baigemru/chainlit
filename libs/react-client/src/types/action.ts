import type { Action } from '../protocol';

/**
 * How a button asks to be drawn. Weight, not colour: the palette is the
 * client's, and an application that spells a colour here would be dressing
 * one theme at the cost of the other.
 *
 * Taken from the generated wire struct rather than re-spelled, so a variant
 * added to the protocol cannot quietly fail to exist here.
 */
export type ActionVariant = NonNullable<Action['variant']>;

export interface IAction {
  label: string;
  forId: string;
  id: string;
  payload: Record<string, unknown>;
  name: string;
  onClick: () => void;
  tooltip: string;
  icon?: string;
  /**
   * Absent means `default`: the server omits the field at its default, and
   * an action built client-side never names one. The single renderer of
   * this field resolves it, which is why it is not defaulted in `toAction`.
   */
  variant?: ActionVariant;
}
