import type {
  IAsk,
  IFeedback,
  IFileRef,
  IMessageElement,
  IStep
} from '@chainlit/react-client';

interface IMessageContext {
  uploadFile?: (
    file: File,
    onProgress: (progress: number) => void
  ) => { xhr: XMLHttpRequest; promise: Promise<IFileRef> };
  cot: 'hidden' | 'tool_call' | 'full';
  cotDisplay: 'list' | 'compact';
  showStepDetails: boolean;
  askUser?: IAsk;
  loading: boolean;
  /**
   * Whether it is the user's turn: `task.indicator.accepting`, not
   * `loading`. The two differ during a `cl.run_in_background` run, which
   * lights the spinner while the person may still type and press buttons.
   * Optional because absent means open, as on the wire: a provider that
   * says nothing about the turn must not lock what it renders.
   */
  accepting?: boolean;
  showFeedbackButtons: boolean;
  uiName: string;
  allowHtml?: boolean;
  latex?: boolean;
  /**
   * Id of the step currently in wait mode: the conversation's last step in
   * document order when it carries the transient `wait` field, undefined
   * otherwise (any newer step deactivates it). Stays permanently undefined —
   * and the context value stable — for apps that never send wait messages.
   */
  activeWaitStepId?: string;
  renderUserMarkdown?: boolean;
  onElementRefClick?: (element: IMessageElement) => void;
  onFeedbackUpdated?: (
    message: IStep,
    onSuccess: () => void,
    feedback: IFeedback
  ) => void;
  onFeedbackDeleted?: (
    message: IStep,
    onSuccess: () => void,
    feedbackId: string
  ) => void;
  onError: (error: string) => void;
}

export type { IMessageContext };
