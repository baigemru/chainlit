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
  highlight?: boolean;
}

export interface IStarterCategory {
  label: string;
  icon?: string;
  starters: IStarter[];
}

export interface ChatProfile {
  default: boolean;
  device?: DeviceKey;
  icon?: string;
  name: string;
  display_name?: string;
  markdown_description: string;
  starters?: IStarter[];
}

export interface IOAuthProviderDetail {
  id: string;
  loginEnabled: boolean;
  registrationEnabled: boolean;
  vkEnabled?: boolean;
  yandexEnabled?: boolean;
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
    user_menu_links?: {
      name: string;
      url: string;
      icon_url?: string;
      icon_url_light?: string;
      icon_url_dark?: string;
      icon_mask?: boolean;
      display_name?: string;
      target?: '_blank' | '_self' | '_parent' | '_top' | 'iframe';
    }[];
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
