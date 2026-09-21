import type { IJsonSchema } from './account';

/**
 * Who a profile or a starter is offered to. Purely visibility: nothing
 * branches on it, and a hidden profile stays perfectly usable when a thread
 * from another device resolves to it.
 */
export type DeviceKey = 'mobile' | 'pc' | 'all';

export interface IStarter {
  label: string;
  message: string;
  icon?: string;
  command?: string;
  device?: DeviceKey;
  /** Switches to this chat profile instead of sending `message`. */
  profile?: string;
  /**
   * An in-app address, and the highest-priority action: `navigate(href)` and
   * nothing else — no session teardown, because the page it opens (the
   * account, say) is a dialog over the chat that is still live behind it.
   */
  href?: string;
  /** The second line: what pressing this will actually do. */
  description?: string;
  /**
   * The note in the corner — a price, a duration. The client renders it and
   * never interprets it: the string is the application's, whole.
   */
  caption?: string;
  /**
   * Offered but not available. The starter stays on screen, dimmed and
   * inert: an offer withdrawn silently is indistinguishable from one that
   * was never made, and the application wants the user to see it coming.
   */
  disabled?: boolean;
  highlight?: boolean;
}

/**
 * Three densities of one list, not three kinds of starter: `tiles` are the
 * compact buttons, `plates` the cards carrying a description and a caption,
 * `rows` the line-per-offer list. A value from a newer backend falls back to
 * `tiles`, the shape every starter has always had.
 */
export type StarterLayout = 'tiles' | 'plates' | 'rows';

export interface IStarterCategory {
  label: string;
  /** The line under the section heading. */
  description?: string;
  icon?: string;
  layout?: StarterLayout;
  /** Fold the section into one summary line on a phone. */
  collapsible?: boolean;
  starters: IStarter[];
}

export interface ChatProfile {
  default: boolean;
  device?: DeviceKey;
  icon?: string;
  name: string;
  display_name?: string;
  markdown_description: string;
  /**
   * Whether the profile is *offered*. `false` is a door: reachable through a
   * starter, a hand-off or a resumed thread, but never proposed in the
   * selector and never the default. Absent means offered — every profile
   * written before this field existed is one.
   */
  listed?: boolean;
  /** Markdown under the composer on an empty chat. */
  composer_hint?: string;
  starters?: IStarter[];
}

/**
 * A login button that skips the provider's own form and goes straight to one
 * identity provider behind it. The deployment declares these in
 * `[[UI.idp_shortcuts]]`; the alias the broker is told stays on the server,
 * so all the browser needs is what to draw and which id to ask for.
 */
export interface IIdpShortcut {
  id: string;
  label: string;
  iconUrl?: string | null;
  iconUrlLight?: string | null;
  iconUrlDark?: string | null;
}

export interface IOAuthProviderDetail {
  id: string;
  loginEnabled: boolean;
  registrationEnabled: boolean;
  /** Empty for a provider that brokers no other identity provider. */
  idpShortcuts?: IIdpShortcut[];
  iconUrl?: string | null;
  iconUrlLight?: string | null;
  iconUrlDark?: string | null;
}

export interface IAuthConfig {
  requireLogin: boolean;
  passwordAuth: boolean;
  headerAuth: boolean;
  oauthProviders: string[];
  oauthProviderDetails?: IOAuthProviderDetail[];
  default_theme?: 'light' | 'dark';
  ui?: IChainlitConfig['ui'];
}

export interface IChainlitConfig {
  markdown?: string;
  ui: {
    name: string;
    description?: string;
    default_theme?: 'light' | 'dark';
    layout?: 'default' | 'wide';
    default_sidebar_state?: 'open' | 'closed' | 'hidden';
    confirm_new_chat?: boolean;
    cot: 'hidden' | 'tool_call' | 'full';
    cot_display?: 'list' | 'compact';
    show_step_details?: boolean;
    /**
     * Whether the composer offers the way back to the chat a profile hand-off
     * came from. Off unless the deployment says otherwise; the return itself
     * keeps working either way.
     */
    show_parent_thread_button?: boolean;
    /**
     * Whether the welcome screen opens with a picture of itself — the
     * profile's icon, or the app logo when no profile names one. `false`
     * starts the screen with the profile's description instead.
     */
    welcome_avatar?: boolean;
    /**
     * Draw `name` as a wordmark at the left of the header. Off unless the
     * deployment asks; a phone draws it only when `mobile_header` names
     * `"wordmark"`, because the wordmark has no overflow rendering to fall
     * back to.
     */
    header_wordmark?: boolean;
    github?: string;
    custom_css?: string;
    custom_js?: string;
    custom_font?: string;
    alert_style?: 'classic' | 'modern';
    login_page_image?: string;
    login_page_image_filter?: string;
    login_page_image_dark_filter?: string;
    forgot_password_url?: string;
    custom_meta_image_url?: string;
    logo_file_url?: string;
    default_avatar_file_url?: string;
    avatar_size?: number;
    header_links?: {
      name: string;
      display_name?: string;
      icon_url?: string;
      icon_url_light?: string;
      icon_url_dark?: string;
      icon_mask?: boolean;
      authenticated_only?: boolean;
      url: string;
      target?: '_blank' | '_self' | '_parent' | '_top';
      label_url?: string;
      label_refresh_interval?: number;
      /** Fold this link into the header overflow menu on a narrow screen. */
      collapse_on_mobile?: boolean;
    }[];
    /**
     * Built-in header buttons a narrow screen keeps; the rest fold into the
     * overflow menu. Absent means the header's own default.
     */
    mobile_header?: string[];
    /**
     * Offer, on a phone, to move to the desktop version. Absent or disabled
     * means nothing is shown: an app that never said so must not surprise its
     * users with a modal on first paint.
     */
    mobile_notice?: {
      enabled: boolean;
      mode: 'dialog' | 'toast';
      title: string;
      text: string;
      link_url: string;
      link_label: string;
      dismiss_label: string;
      frequency: 'session' | 'once' | 'always';
    };
    /**
     * The account page. Absent means the row in the user menu is not shown —
     * the same convention `mobile_notice` follows, so a deployment that never
     * declared the table grows no menu entry it cannot serve.
     */
    account?: {
      enabled: boolean;
      title: string;
      /**
       * The page's JSON Schema, as the settings controller emits it from the
       * Struct the application registered. The dialog fetches its own copy
       * with the values; this one is for everything that has to know the
       * sections *before* the dialog is opened.
       */
      schema?: IJsonSchema;
    };
  };
  features: {
    spontaneous_file_upload?: {
      enabled?: boolean;
      max_size_mb?: number;
      max_files?: number;
      accept?: string[] | Record<string, string[]>;
    };
    unsafe_allow_html?: boolean;
    user_message_autoscroll?: boolean;
    assistant_message_autoscroll?: boolean;
    assistant_message_anchor?: 'bottom' | 'top';
    latex?: boolean;
    user_message_markdown?: boolean;
  };
  debugUrl?: string;
  userEnv: string[];
  maskUserEnv?: boolean;
  dataPersistence: boolean;
  threadResumable: boolean;
  threadSharing?: boolean;
  chatProfiles: ChatProfile[];
  starters?: IStarter[];
  starterCategories?: IStarterCategory[];

  translation: object;
}
