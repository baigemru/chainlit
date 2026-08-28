"""Application configuration: ``.chainlit/config.toml`` plus what the CLI
and the ``@cl.*`` decorators register at runtime.

The TOML sections are ``msgspec.Struct`` types, decoded with
``msgspec.convert`` so a value of the wrong type or a literal outside its
choices is refused at startup, where a misspelled config is cheapest to fix.
Unknown keys are ignored on purpose: a ``config.toml`` written by an older
release still carries retired tables (``[features.mcp]``, ``[features.slack]``,
``hot_swap_chat_profile``) and must keep loading.

``ChainlitConfig`` itself is a plain class, not a Struct: it is never decoded,
the CLI and ``reload_config`` reassign its sections, and tests patch methods
on it.
"""

import json
import os
import site
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import util
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Union,
)

import msgspec
from msgspec import NODEFAULT, Struct, structs

from chainlit.logger import logger
from chainlit.translations import lint_translation_json
from chainlit.version import __version__

from ._utils import is_path_inside

if TYPE_CHECKING:
    from chainlit.action import Action
    from chainlit.message import Message
    from chainlit.types import (
        ChatProfile,
        Feedback,
        Starter,
        StarterCategory,
        ThreadDict,
    )
    from chainlit.user import User

BACKEND_ROOT = os.path.dirname(__file__)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(BACKEND_ROOT))
TRANSLATIONS_DIR = os.path.join(BACKEND_ROOT, "translations")


# Get the directory the script is running from
APP_ROOT = os.getenv("CHAINLIT_APP_ROOT", os.getcwd())

# Create the directory to store the uploaded files
FILES_DIRECTORY = Path(APP_ROOT) / ".files"
FILES_DIRECTORY.mkdir(exist_ok=True)

config_dir = os.path.join(APP_ROOT, ".chainlit")
public_dir = os.path.join(APP_ROOT, "public")
config_file = os.path.join(config_dir, "config.toml")
config_translation_dir = os.path.join(config_dir, "translations")

# Default config file created if none exists
DEFAULT_CONFIG_STR = f"""[project]
# List of environment variables to be provided by each user to use the app.
user_env = []

# Duration (in seconds) during which the session is saved when the connection is lost
session_timeout = 3600

# Duration (in seconds) of the user session expiry
user_session_timeout = 1296000  # 15 days

# Enable third parties caching (e.g., LangChain cache)
cache = false

# Whether to persist user environment variables (API keys) to the database
# Set to true to store user env vars in DB, false to exclude them for security
persist_user_env = false

# Whether to mask user environment variables (API keys) in the UI with password type
# Set to true to show API keys as ***, false to show them as plain text
mask_user_env = false

# Authorized origins
allow_origins = ["*"]

[features]
# Process and display HTML in messages. This can be a security risk (see https://stackoverflow.com/questions/19603097/why-is-it-dangerous-to-render-user-generated-html-or-javascript)
unsafe_allow_html = false

# Process and display mathematical expressions. This can clash with "$" characters in messages.
latex = false

# Enable rendering of user messages markdown
user_message_markdown = true

# Autoscroll new user messages at the top of the window
user_message_autoscroll = true

# Autoscroll new assistant messages
assistant_message_autoscroll = true

# Automatically tag threads with the current chat profile (if a chat profile is used)
auto_tag_thread = true

# Allow users to edit their own messages
edit_message = true

# Allow users to share threads (backend + UI). Requires an app-defined on_shared_thread_view callback.
allow_thread_sharing = false

# Enable favorite messages
favorites = false

# Authorize users to spontaneously upload files with messages
[features.spontaneous_file_upload]
    enabled = true
    # Define accepted file types using MIME types
    # Examples:
    # 1. For specific file types:
    #    accept = ["image/jpeg", "image/png", "application/pdf"]
    # 2. For all files of certain type:
    #    accept = ["image/*", "audio/*", "video/*"]
    # 3. For specific file extensions:
    #    accept = {{ "application/octet-stream" = [".xyz", ".pdb"] }}
    # Note: Using "*/*" is not recommended as it may cause browser warnings
    accept = ["*/*"]
    max_files = 20
    max_size_mb = 500

[features.audio]
    # Enable audio features
    enabled = false
    # Sample rate of the audio
    sample_rate = 24000

[UI]
# Name of the assistant.
name = "Assistant"

# default_theme = "dark"

# Force a specific language for all users (e.g., "en-US", "he-IL", "fr-FR")
# If not set, the browser's language will be used
# language = "en-US"

# layout = "wide"

# default_sidebar_state = "open"  # Options: "open", "closed", "hidden"

# Chat settings display location: "message_composer" (default) or "sidebar" (header)
# chat_settings_location = "message_composer"

# Default state of chat settings sidebar when location is "sidebar"
# default_chat_settings_open = false

# Whether to prompt user confirmation on clicking 'New Chat'
confirm_new_chat = true

# Description of the assistant. This is used for HTML tags.
# description = ""

# Chain of Thought (CoT) display mode. Can be "hidden", "tool_call" or "full".
cot = "full"

# CoT display layout. "list" shows each step individually, "compact" collapses into one summary line.
# cot_display = "list"

# Whether steps are expandable to show input/output details.
# show_step_details = true

# Specify a CSS file that can be used to customize the user interface.
# The CSS file can be served from the public directory or via an external link.
# custom_css = "/public/test.css"

# Specify additional attributes for a custom CSS file
# custom_css_attributes = "media=\\\"print\\\""

# Specify a JavaScript file that can be used to customize the user interface.
# The JavaScript file can be served from the public directory.
# custom_js = "/public/test.js"

# The style of alert boxes. Can be "classic" or "modern".
alert_style = "classic"

# Specify additional attributes for custom JS file
# custom_js_attributes = "async type = \\\"module\\\""

# Custom login page image, relative to public directory or external URL
# login_page_image = "/public/custom-background.jpg"

# Custom login page image filter (Tailwind internal filters, no dark/light variants)
# login_page_image_filter = "brightness-50 grayscale"
# login_page_image_dark_filter = "contrast-200 blur-sm"

# Specify a custom meta URL (used for meta tags like og:url)
# custom_meta_url = "https://github.com/Chainlit/chainlit"

# Specify a custom meta image url.
# custom_meta_image_url = "https://chainlit-cloud.s3.eu-west-3.amazonaws.com/logo/chainlit_banner.png"

# Load assistant logo directly from URL.
logo_file_url = ""

# Load assistant avatar image directly from URL.
default_avatar_file_url = ""

# Avatar size in pixels (default: 20).
# avatar_size = 20

# Specify a custom build directory for the frontend.
# This can be used to customize the frontend code.
# Be careful: If this is a relative path, it should not start with a slash.
# custom_build = "./public/build"

# Optional link to a "Forgot password?" page, shown under the password field
# of the login form. Can also be set with the CHAINLIT_FORGOT_PASSWORD_URL
# environment variable, which takes precedence.
# login_page_forgot_password_url = "https://example.com/reset-password"

# Specify optional one or more custom links in the header.
# [[UI.header_links]]
#     name = "Issues"
#     display_name = "Report Issue"
#     icon_url = "https://avatars.githubusercontent.com/u/128686189?s=200&v=4"  # Optional.
#     icon_url_light = "/public/icon_light.svg"  # Optional. Icon for the light theme; icon_url is the fallback.
#     icon_url_dark = "/public/icon_dark.svg"    # Optional. Icon for the dark theme; icon_url is the fallback.
#     icon_mask = false            # Optional. Render the icon with the current text color (theme-aware); requires a monochrome icon.
#     authenticated_only = false   # Optional. Only show the link to authenticated users.
#     url = "https://github.com/Chainlit/chainlit/issues"
#     target = "_blank" (default)  # Optional: "_self", "_parent", "_top".
#     label_url = "/my/endpoint"   # Optional. Endpoint returning {{"label": "..."}} used as the button text; a click re-fetches instead of navigating.
#     label_refresh_interval = 60  # Optional. Re-fetch the label every N seconds.

# Specify optional one or more custom links inside the user menu (the dropdown
# opened by clicking the avatar in the top-right corner).
# [[UI.user_menu_links]]
#     name = "Account"
#     url = "https://example.com/account"
#     icon_url = "/public/account.svg"     # Optional. Renders to the right of the label.
#     icon_url_light = "/public/account_light.svg"  # Optional. Icon for the light theme; icon_url is the fallback.
#     icon_url_dark = "/public/account_dark.svg"    # Optional. Icon for the dark theme; icon_url is the fallback.
#     icon_mask = false                    # Optional. Render the icon with the current text color (theme-aware); requires a monochrome icon.
#     display_name = "Manage account"      # Optional. Defaults to `name`.
#     target = "_blank"                    # Optional, defaults to "_blank". "_self", "_parent", "_top".

[meta]
generated_by = "{__version__}"
"""


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_ROOT_PATH = ""


class Settings(Struct, kw_only=True):
    """Base of every TOML section.

    ``kw_only`` because the sections are built from tables, never
    positionally, and a positional constructor would silently bind a value
    to the wrong field when one is added.
    """


class RunSettings(Settings):
    """What the ``chainlit run`` command line sets; never read from TOML."""

    # Name of the module (python file) used in the run command
    module_name: Optional[str] = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = None
    root_path: str = DEFAULT_ROOT_PATH
    headless: bool = False
    watch: bool = False
    no_cache: bool = False
    debug: bool = False
    ci: bool = False


class SpontaneousFileUploadFeature(Settings):
    enabled: Optional[bool] = None
    accept: Optional[Union[List[str], Dict[str, List[str]]]] = None
    max_files: Optional[int] = None
    max_size_mb: Optional[int] = None


class AudioFeature(Settings):
    sample_rate: int = 24000
    enabled: bool = False


class FeaturesSettings(Settings):
    spontaneous_file_upload: Optional[SpontaneousFileUploadFeature] = None
    audio: Optional[AudioFeature] = msgspec.field(default_factory=AudioFeature)
    latex: bool = False
    user_message_markdown: bool = True
    user_message_autoscroll: bool = True
    assistant_message_autoscroll: bool = True
    unsafe_allow_html: bool = False
    auto_tag_thread: bool = True
    edit_message: bool = True
    allow_thread_sharing: bool = False
    favorites: bool = False
    # Turn the "ask slot is busy" refusal into AskSlotBusyError instead of
    # a None return. Off by default: None is also what a timeout and an
    # empty answer produce, and existing apps branch on it.
    strict_ask_slot: bool = False


class HeaderLink(Settings):
    name: str
    url: str
    icon_url: Optional[str] = None
    # Per-theme icon overrides; icon_url is the fallback for both themes.
    icon_url_light: Optional[str] = None
    icon_url_dark: Optional[str] = None
    # Render the icon through a CSS mask filled with the current text color,
    # so it follows the active theme. Requires a monochrome icon.
    icon_mask: bool = False
    display_name: Optional[str] = None
    # Only show the link to authenticated users.
    authenticated_only: bool = False
    target: Optional[Literal["_blank", "_self", "_parent", "_top"]] = None
    # Endpoint returning {"label": "..."} used as the button text. When set,
    # the label is fetched on mount, a click re-fetches it instead of
    # navigating, and `display_name` is shown until the first response.
    label_url: Optional[str] = None
    # Re-fetch the label every N seconds; None disables periodic refresh.
    label_refresh_interval: Optional[int] = None


class UserMenuLink(Settings):
    name: str
    url: str
    icon_url: Optional[str] = None
    # Per-theme icon overrides; icon_url is the fallback for both themes.
    icon_url_light: Optional[str] = None
    icon_url_dark: Optional[str] = None
    # Render the icon through a CSS mask filled with the current text color,
    # so it follows the active theme. Requires a monochrome icon.
    icon_mask: bool = False
    display_name: Optional[str] = None
    target: Optional[Literal["_blank", "_self", "_parent", "_top", "iframe"]] = None


class UISettings(Settings):
    name: str
    description: str = ""
    cot: Literal["hidden", "tool_call", "full"] = "full"
    cot_display: Literal["list", "compact"] = "list"
    show_step_details: bool = True
    default_theme: Optional[Literal["light", "dark"]] = "dark"
    language: Optional[str] = None
    layout: Optional[Literal["default", "wide"]] = "default"
    default_sidebar_state: Optional[Literal["open", "closed", "hidden"]] = "open"
    chat_settings_location: Optional[Literal["message_composer", "sidebar"]] = (
        "message_composer"
    )
    default_chat_settings_open: bool = False
    confirm_new_chat: bool = True
    github: Optional[str] = None
    custom_css: Optional[str] = None
    custom_css_attributes: Optional[str] = ""
    custom_js: Optional[str] = None

    alert_style: Optional[Literal["classic", "modern"]] = "classic"
    custom_js_attributes: Optional[str] = "defer"
    login_page_image: Optional[str] = None
    login_page_image_filter: Optional[str] = None
    login_page_image_dark_filter: Optional[str] = None
    login_page_forgot_password_url: Optional[str] = None

    custom_meta_url: Optional[str] = None
    custom_meta_image_url: Optional[str] = None
    logo_file_url: Optional[str] = None
    default_avatar_file_url: Optional[str] = None
    avatar_size: Optional[int] = None
    custom_build: Optional[str] = None
    header_links: Optional[List[HeaderLink]] = None
    user_menu_links: Optional[List[UserMenuLink]] = None


@dataclass
class CodeSettings:
    """The callbacks the ``@cl.*`` decorators register.

    A dataclass rather than a Struct: it is filled in one attribute at a
    time as the app module imports, holds callables no codec has any
    business seeing, and is replaced wholesale on ``reload_config``.
    """

    # App action functions
    action_callbacks: Dict[str, Callable[["Action"], Any]] = field(default_factory=dict)

    # Module object loaded from the module_name
    module: Any = None

    # App life cycle callbacks
    on_app_startup: Optional[Callable[[], Union[Awaitable[None], None]]] = None
    on_app_shutdown: Optional[Callable[[], Union[Awaitable[None], None]]] = None

    # Session life cycle callbacks
    on_stop: Optional[Callable[[], Any]] = None
    on_chat_start: Optional[Callable[[], Any]] = None
    on_chat_end: Optional[Callable[[], Any]] = None
    on_chat_resume: Optional[Callable[["ThreadDict"], Any]] = None
    on_thread_ready: Optional[Callable[["ThreadDict"], Any]] = None
    on_message: Optional[Callable[["Message"], Any]] = None
    on_feedback: Optional[Callable[["Feedback"], Any]] = None
    set_chat_profiles: Optional[
        Callable[[Optional["User"], Optional["str"]], Awaitable[List["ChatProfile"]]]
    ] = None
    set_starters: Optional[
        Callable[[Optional["User"], Optional["str"]], Awaitable[List["Starter"]]]
    ] = None
    set_starter_categories: Optional[
        Callable[
            [Optional["User"], Optional["str"], Optional["str"]],
            Awaitable[List["StarterCategory"]],
        ]
    ] = None
    on_shared_thread_view: Optional[
        Callable[["ThreadDict", Optional["User"]], Awaitable[bool]]
    ] = None
    # Auth callbacks
    password_auth_callback: Optional[
        Callable[[str, str], Awaitable[Optional["User"]]]
    ] = None
    oauth_callback: Optional[
        Callable[[str, str, Dict[str, str], "User"], Awaitable[Optional["User"]]]
    ] = None

    # Helpers
    author_rename: Optional[Callable[[str], Awaitable[str]]] = None


class ProjectSettings(Settings):
    allow_origins: List[str] = msgspec.field(default_factory=lambda: ["*"])
    # List of environment variables to be provided by each user to use the app. If empty, no environment variables will be asked to the user.
    user_env: Optional[List[str]] = None
    # Path to the local langchain cache database
    lc_cache_path: Optional[str] = None
    # Duration (in seconds) during which the session is saved when the connection is lost
    session_timeout: int = 300
    # Duration (in seconds) of the user session expiry
    user_session_timeout: int = 1296000  # 15 days
    # Enable third parties caching (e.g LangChain cache)
    cache: bool = False
    # Whether to persist user environment variables (API keys) to the database
    persist_user_env: Optional[bool] = False
    # Whether to mask user environment variables (API keys) in the UI with password type
    mask_user_env: Optional[bool] = False


class ChainlitConfigOverrides(Settings):
    """What a chat profile changes in the config the UI is handed.

    Each section is the ordinary settings type, so an app writes
    ``UISettings(name="...")`` and only ``name`` overrides; see
    :meth:`ChainlitConfig.with_overrides` for what "only" means.
    """

    ui: Optional[UISettings] = None
    features: Optional[FeaturesSettings] = None
    project: Optional[ProjectSettings] = None


def _overlay(base: Struct, override: Struct) -> Struct:
    """``base`` with the fields ``override`` sets, recursing into sections.

    A field counts as set when it differs from its class default. Pydantic
    tracked which keyword arguments were passed; a Struct does not, and the
    override is built with the plain settings types, so the default is the
    only baseline there is. The hole this leaves -- overriding a field back
    to its own default is a no-op -- is accepted: a required field (a
    profile's ``name``) has no default and is always applied, and nested
    sections merge rather than replace, so ``SpontaneousFileUploadFeature(
    enabled=False)`` keeps the base ``accept`` and ``max_files``.
    """
    changes: Dict[str, Any] = {}
    for spec in structs.fields(type(override)):
        value = getattr(override, spec.name)
        if spec.default is not NODEFAULT:
            default = spec.default
        elif spec.default_factory is not NODEFAULT:
            default = spec.default_factory()
        else:
            default = NODEFAULT
        if default is not NODEFAULT and value == default:
            continue
        current = getattr(base, spec.name)
        if isinstance(value, Struct) and isinstance(current, Struct):
            value = _overlay(current, value)
        changes[spec.name] = value
    return structs.replace(base, **changes)


class ChainlitConfig:
    """The whole runtime configuration, one instance per process.

    The TOML sections are decoded once by :func:`load_config`; ``run`` is
    written by the CLI and ``code`` by the decorators as the app imports.
    """

    __slots__ = ("code", "features", "project", "root", "run", "ui")

    def __init__(
        self,
        *,
        features: FeaturesSettings,
        ui: UISettings,
        project: ProjectSettings,
        code: Optional[CodeSettings] = None,
        run: Optional[RunSettings] = None,
        root: str = APP_ROOT,
    ) -> None:
        self.root = root
        self.run = run if run is not None else RunSettings()
        self.features = features
        self.ui = ui
        self.project = project
        self.code = code if code is not None else CodeSettings()

    def load_translation(self, language: str):
        translation = {}
        default_language = "en-US"
        parent_language = language.split("-")[0]

        translation_dir = Path(config_translation_dir)

        # 1. Exact match (e.g. "da-DK.json" or "da.json")
        translation_lib_file_path = translation_dir / f"{language}.json"
        if (
            is_path_inside(translation_lib_file_path, translation_dir)
            and translation_lib_file_path.is_file()
        ):
            translation = json.loads(
                translation_lib_file_path.read_text(encoding="utf-8")
            )
            return translation

        # 2. Parent/base language fallback (e.g. "de-DE" → "de.json")
        translation_lib_parent_language_file_path = (
            translation_dir / f"{parent_language}.json"
        )
        if (
            is_path_inside(translation_lib_parent_language_file_path, translation_dir)
            and translation_lib_parent_language_file_path.is_file()
        ):
            logger.warning(
                f"Translation file for {language} not found. Using parent translation {parent_language}."
            )
            translation = json.loads(
                translation_lib_parent_language_file_path.read_text(encoding="utf-8")
            )
            return translation

        # 3. Regional variant lookup (e.g. "da" → "da-DK.json")
        if language == parent_language:
            for candidate in sorted(translation_dir.glob(f"{parent_language}-*.json")):
                if is_path_inside(candidate, translation_dir) and candidate.is_file():
                    variant = candidate.stem
                    logger.info(
                        f"Translation file for {language} not found. Using regional variant {variant}."
                    )
                    translation = json.loads(candidate.read_text(encoding="utf-8"))
                    return translation

        # 4. Default fallback
        default_translation_lib_file_path = translation_dir / f"{default_language}.json"
        if (
            is_path_inside(default_translation_lib_file_path, translation_dir)
            and default_translation_lib_file_path.is_file()
        ):
            logger.warning(
                f"Translation file for {language} not found. Using default translation {default_language}."
            )
            translation = json.loads(
                default_translation_lib_file_path.read_text(encoding="utf-8")
            )

        return translation

    def with_overrides(
        self, overrides: Optional[ChainlitConfigOverrides]
    ) -> "ChainlitConfig":
        """A copy with a profile's overrides applied; ``self`` is untouched.

        ``code`` and ``run`` are shared, not copied: the callbacks and the
        command line are per process, not per profile.
        """
        if overrides is None:
            return self
        sections: Dict[str, Any] = {}
        for name in ("ui", "features", "project"):
            patch = getattr(overrides, name)
            current = getattr(self, name)
            sections[name] = _overlay(current, patch) if patch else current
        return ChainlitConfig(root=self.root, run=self.run, code=self.code, **sections)


def init_config(log: bool = False):
    """Initialize the configuration file if it doesn't exist."""
    if not os.path.exists(config_file):
        os.makedirs(config_dir, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_STR)
            logger.info(f"Created default config file at {config_file}")
    elif log:
        logger.info(f"Config file already exists at {config_file}")

    if not os.path.exists(config_translation_dir):
        os.makedirs(config_translation_dir, exist_ok=True)
        logger.info(
            f"Created default translation directory at {config_translation_dir}"
        )

    for file in os.listdir(TRANSLATIONS_DIR):
        if file.endswith(".json"):
            dst = os.path.join(config_translation_dir, file)
            if not os.path.exists(dst):
                src = os.path.join(TRANSLATIONS_DIR, file)
                with open(src, encoding="utf-8") as f:
                    translation = json.load(f)
                    with open(dst, "w", encoding="utf-8") as f:
                        json.dump(translation, f, indent=4)
                        logger.info(f"Created default translation file at {dst}")


def load_module(target: str, force_refresh: bool = False):
    """Load the specified module."""

    # Get the target's directory
    target_dir = os.path.dirname(os.path.abspath(target))

    # Add the target's directory to the Python path
    sys.path.insert(0, target_dir)

    if force_refresh:
        # Get current site packages dirs
        site_package_dirs = site.getsitepackages()

        # Clear the modules related to the app from sys.modules
        for module_name, module in list(sys.modules.items()):
            if (
                hasattr(module, "__file__")
                and module.__file__
                and module.__file__.startswith(target_dir)
                and not any(module.__file__.startswith(p) for p in site_package_dirs)
            ):
                sys.modules.pop(module_name, None)

    spec = util.spec_from_file_location(target, target)
    if not spec or not spec.loader:
        sys.path.pop(0)
        return

    module = util.module_from_spec(spec)
    if not module:
        sys.path.pop(0)
        return

    spec.loader.exec_module(module)

    sys.modules[target] = module

    # Remove the target's directory from the Python path
    sys.path.pop(0)


def decode_settings(toml_dict: Dict[str, Any]) -> Dict[str, Any]:
    """The sections of a parsed ``config.toml``, as the settings types.

    Separate from :func:`load_settings` so a config can be checked without a
    file on disk. Keys the sections do not declare are dropped by
    ``msgspec.convert`` -- that is what lets a config from an older release
    keep loading -- while a wrong type or an unknown literal raises
    ``msgspec.ValidationError`` naming the key.
    """
    project_config = dict(toml_dict.get("project", {}))
    features_config = toml_dict.get("features", {})
    ui_config = toml_dict.get("UI", {})
    meta = toml_dict.get("meta")

    if not meta or meta.get("generated_by") <= "0.3.0":
        raise ValueError(
            f"Your config file '{config_file}' is outdated. Please delete it and restart the app to regenerate it."
        )

    project_config["lc_cache_path"] = os.path.join(config_dir, ".langchain.db")

    return {
        "features": msgspec.convert(features_config, type=FeaturesSettings),
        "ui": msgspec.convert(ui_config, type=UISettings),
        "project": msgspec.convert(project_config, type=ProjectSettings),
        "code": CodeSettings(),
    }


def load_settings() -> Dict[str, Any]:
    """The TOML sections of ``config_file``, decoded and validated."""
    with open(config_file, "rb") as f:
        toml_dict = tomllib.load(f)
    return decode_settings(toml_dict)


def reload_config():
    """Reload the configuration from the config file.

    Everything but ``run.module_name`` starts over: the watcher re-imports
    the app module right after, which re-registers ``code``, and it needs
    the module name to do so.
    """
    global config
    if config is None:
        return

    original_module_name = config.run.module_name if config.run else None

    new_cfg = ChainlitConfig(**load_settings())
    config.root = new_cfg.root
    config.run = new_cfg.run
    config.features = new_cfg.features
    config.ui = new_cfg.ui

    if original_module_name and config.run:
        config.run.module_name = original_module_name
    config.project = new_cfg.project
    config.code = new_cfg.code


def load_config():
    """Load the configuration from the config file."""
    init_config()
    settings = load_settings()
    return ChainlitConfig(**settings)


def lint_translations():
    # Load the ground truth (en-US.json file from chainlit source code)
    src = os.path.join(TRANSLATIONS_DIR, "en-US.json")
    with open(src, encoding="utf-8") as f:
        truth = json.load(f)

        # Find the local app translations
        for file in os.listdir(config_translation_dir):
            if file.endswith(".json"):
                # Load the translation file
                to_lint = os.path.join(config_translation_dir, file)
                with open(to_lint, encoding="utf-8") as f2:
                    translation = json.load(f2)

                    # Lint the translation file
                    lint_translation_json(file, truth, translation)


config = load_config()
