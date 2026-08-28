export interface IAction {
  label: string;
  forId: string;
  id: string;
  payload: Record<string, unknown>;
  name: string;
  onClick: () => void;
  tooltip: string;
  icon?: string;
}
