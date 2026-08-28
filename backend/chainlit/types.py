"""The shapes the ``cl.*`` API hands to and takes from application code.

Dictionaries and plain dataclasses only. The wire has its own structs in
``chainlit.protocol``; what lives here is what an application author sees --
the ``ThreadDict`` a resume hook receives, the ``ChatProfile`` a callback
returns, the response of an ask. Nothing here validates: the conversion at
the emitter is the validation, and a second schema in front of it would be a
second thing to keep in step.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Literal,
    NotRequired,
    Optional,
    TypedDict,
)

if TYPE_CHECKING:
    from chainlit.element import ElementDict
    from chainlit.step import StepDict

InputWidgetType = Literal[
    "switch",
    "slider",
    "select",
    "textinput",
    "tags",
    "numberinput",
    "multiselect",
    "checkbox",
    "radio",
    "datepicker",
]
ToastType = Literal["info", "success", "warning", "error"]


class AskSlotBusyError(Exception):
    """Raised by `send_ask_user` when the session's ask slot is taken.

    Only under `features.strict_ask_slot`. Without it the refusal returns
    `None`, which a caller cannot tell apart from a timeout or an empty
    answer — the ambiguity that turns "another question is in flight" into
    "the user declined".
    """

    def __init__(self, step_id: str) -> None:
        self.step_id = step_id
        super().__init__(f"An ask is already pending (step {step_id})")


class ThreadDict(TypedDict):
    id: str
    createdAt: str
    name: Optional[str]
    userId: Optional[str]
    userIdentifier: Optional[str]
    tags: Optional[List[str]]
    metadata: Optional[Dict]
    steps: List["StepDict"]
    elements: Optional[List["ElementDict"]]
    # Thread this one was switched from, when spawned by set_chat_profile.
    parentThreadId: NotRequired[Optional[str]]


class FileDict(TypedDict):
    """One spooled upload, as ``Session.files`` holds it."""

    id: str
    name: str
    path: Path
    size: int
    type: str


@dataclass
class AskFileResponse:
    id: str
    name: str
    path: str
    size: int
    type: str


class AskActionResponse(TypedDict):
    name: str
    payload: Dict
    label: str
    tooltip: str
    forId: str
    id: str


class AskElementResponse(TypedDict, total=False):
    """Reply of a custom-element ask: the props travel nested, not spread."""

    submitted: bool
    props: Dict[str, Any]


class _AsDict:
    """``to_dict`` for the dataclasses the project controller serialises."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


@dataclass
class Starter(_AsDict):
    """Specification for a starter that can be chosen by the user at the thread start."""

    label: str
    message: str
    command: Optional[str] = None
    icon: Optional[str] = None


@dataclass
class StarterCategory(_AsDict):
    """A category/group of starters with an optional icon."""

    label: str
    icon: Optional[str] = None
    starters: List[Starter] = field(default_factory=list)


@dataclass
class ChatProfile(_AsDict):
    """Specification for a chat profile that can be chosen by the user at the thread start."""

    name: str
    markdown_description: str
    icon: Optional[str] = None
    display_name: Optional[str] = None
    default: bool = False
    starters: Optional[List[Starter]] = None
    config_overrides: Any = None


class CommandDict(TypedDict):
    # The identifier of the command, will be displayed in the UI
    id: str
    # The description of the command, will be displayed in the UI
    description: str
    # The lucide icon name
    icon: str
    # Display the command as a button in the composer
    button: Optional[bool]
    # Whether the command will be persistent unless the user toggles it
    persistent: Optional[bool]
    # Whether the command should be pre-selected when loaded
    selected: Optional[bool]


class FeedbackDict(TypedDict):
    forId: str
    id: Optional[str]
    value: Literal[0, 1]
    comment: Optional[str]


@dataclass
class Feedback:
    forId: str
    value: Literal[0, 1]
    threadId: Optional[str] = None
    id: Optional[str] = None
    comment: Optional[str] = None
