export type IElement =
  | IImageElement
  | ITextElement
  | IPdfElement
  | ITasklistElement
  | IAudioElement
  | IVideoElement
  | IFileElement
  | IPlotlyElement
  | IDataframeElement
  | ICustomElement;

export type IMessageElement =
  | IImageElement
  | ITextElement
  | IPdfElement
  | IAudioElement
  | IVideoElement
  | IFileElement
  | IPlotlyElement
  | IDataframeElement
  | ICustomElement;

export type ElementType = IElement['type'];
export type IElementSize = 'small' | 'medium' | 'large';

interface TElement<T> {
  id: string;
  type: T;
  threadId?: string;
  forId: string;
  mime?: string;
  url?: string;
  chainlitKey?: string;
}

interface TMessageElement<T> extends TElement<T> {
  name: string;
  display: 'inline' | 'side' | 'page';
}

export interface IImageElement extends TMessageElement<'image'> {
  size?: IElementSize;
}

export interface ITextElement extends TMessageElement<'text'> {
  language?: string;
}

export interface IPdfElement extends TMessageElement<'pdf'> {
  page?: number;
}

export interface IAudioElement extends TMessageElement<'audio'> {
  autoPlay?: boolean;
}

export interface IVideoElement extends TMessageElement<'video'> {
  size?: IElementSize;

  /**
   * Override settings for each type of player in ReactPlayer
   * https://github.com/cookpete/react-player?tab=readme-ov-file#config-prop
   * @type {object}
   */
  playerConfig?: object;
}

export interface IFileElement extends TMessageElement<'file'> {
  type: 'file';
}

export type IPlotlyElement = TMessageElement<'plotly'>;

export type ITasklistElement = TElement<'tasklist'>;

export type IDataframeElement = TMessageElement<'dataframe'>;

export interface ICustomElement extends TMessageElement<'custom'> {
  props: Record<string, unknown>;
}

/**
 * One tab of the element panel, with its contents resolved.
 *
 * The wire's `SidebarSlotRef` names elements by id; this is the same slot
 * after the `sidebar.state` handler has looked those ids up in the element
 * atom. Components render this and never the frame.
 */
export interface IElementSidebarSlot {
  id: string;
  title: string;
  elements: IMessageElement[];
  /** Whether the user may dismiss this tab. */
  closable: boolean;
  /** Drawn without a frame or a header. Was `title === 'canvas'`. */
  canvas: boolean;
}

/**
 * The whole element panel. Mirrors the server's `SidebarState`, and like it
 * never has a "closed" value: an empty panel is empty slots, and `visible`
 * says whether it is on screen — which is how putting it away stopped
 * meaning throwing its contents out.
 */
export interface IElementSidebarState {
  slots: IElementSidebarSlot[];
  active: string | null;
  visible: boolean;
  /**
   * The `rev` of the last `sidebar.state` this client was shown, quoted back
   * in every `sidebar.user`. Not bumped by a local dispatch: what the server
   * needs to know is which of *its* states the user was looking at.
   */
  rev: number;
}
