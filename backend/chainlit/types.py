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
    Sequence,
    TypedDict,
)

if TYPE_CHECKING:
    from chainlit.element import ElementDict
    from chainlit.step import StepDict

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


class AskElementResponse(Dict[str, Any]):
    """Reply of a custom-element ask: the element's props, plus ``submitted``.

    The wire nests the props (a closed struct cannot spread arbitrary keys);
    the application gets them spread, the shape it has always read --
    ``response["keywords"]`` -- with ``submitted`` alongside.
    """


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
    # "mobile" | "pc" | "all". Deliberately untyped and unvalidated: the client
    # shows a starter whose device it does not recognise, so a value added here
    # later never silently hides a starter on an older frontend.
    device: str = "all"
    # Clicking opens this chat profile instead of sending ``message``; the
    # transition is the client's business, the server only advertises it.
    profile: Optional[str] = None
    highlight: bool = False
    # The second line: what the click will actually do. ``label`` names the
    # starter, this one promises the outcome.
    description: Optional[str] = None
    # A short note the client sets apart from the prose -- a price, a
    # duration, a count. What it says is the application's business; nothing
    # in the fork knows what a starter costs or how long it takes.
    caption: Optional[str] = None
    # Shown, and refuses the click. A starter an application cannot honour
    # yet is better inert than absent: a list that changes shape between two
    # visits is a list nobody learns.
    disabled: bool = False
    # An address inside the application -- ``/account?tab=items``. The client
    # navigates there and the chat is left alone.
    #
    # ``href``, ``profile`` and ``message`` are three different answers to
    # the same click, and the client takes the first it is given: ``href``,
    # then ``profile``, then ``message``. The server does not refuse a
    # starter that sets two, for the reason ``device`` is a bare string --
    # a rule enforced here makes the *older* side the one that rejects a
    # newer application's starter.
    href: Optional[str] = None


@dataclass
class StarterCategory(_AsDict):
    """A category/group of starters with an optional icon."""

    label: str
    icon: Optional[str] = None
    starters: List[Starter] = field(default_factory=list)
    # A line under the heading, saying what this group is for.
    description: Optional[str] = None
    # "tiles" | "plates" | "rows" -- three densities of the same list.
    # Untyped and unvalidated for the reason ``Starter.device`` is: a layout
    # added here later has to degrade to ``tiles`` on a frontend that
    # predates it, and it cannot degrade to anything if this side refuses to
    # serve it in the first place.
    layout: str = "tiles"
    # Lets the client fold the group behind its heading. Whether it *is*
    # folded is the client's call -- a phone folds, a desktop does not -- so
    # this is permission, not state.
    collapsible: bool = False


@dataclass
class ChatProfile(_AsDict):
    """Specification for a chat profile that can be chosen by the user at the thread start."""

    name: str
    markdown_description: str
    icon: Optional[str] = None
    display_name: Optional[str] = None
    default: bool = False
    starters: Optional[List[Starter]] = None
    # Hides the profile from the switcher on the other device, nothing more: a
    # thread already in a hidden profile still resumes and still runs.
    device: str = "all"
    config_overrides: Any = None
    # False makes a door rather than a room: the profile is not offered in
    # the switcher and must not be picked as the default, but a starter's
    # ``profile=``, a server-side handoff and a resumed thread all still
    # reach it. ``listed=False`` together with ``default=True`` is an
    # application configuration error -- it names a landing place nobody can
    # land in -- and the fork does not arbitrate: it advertises both flags
    # and lets the client's own default-picking skip what is not listed.
    listed: bool = True
    # Markdown under the composer on an empty chat, in this profile only.
    # How to talk to it, as against ``markdown_description``, which says
    # what it is.
    composer_hint: Optional[str] = None


DEVICE_CLASSES: tuple[str, ...] = ("mobile", "pc")
"""The screen classes a ``device`` label may name. A tablet counts as ``pc``."""


def matches_device(label: Optional[str], device: str) -> bool:
    """Whether an offer labelled ``label`` is meant for this screen class.

    The rule the client applies (``matchesDevice`` in
    ``frontend/src/hooks/use-mobile.tsx``), stated once so an application
    reasoning about its own profiles reasons the same way the browser will.
    A label nobody recognises is shown everywhere -- showing one offer too
    many beats swallowing the only one there was, and it is what lets a
    newer application's label reach an older frontend.
    """
    if not label or label == "all":
        return True
    if label in DEVICE_CLASSES:
        return label == device
    return True


def is_listed(profile: ChatProfile) -> bool:
    """Whether the profile is proposed at all, as opposed to merely reachable."""
    return getattr(profile, "listed", True) is not False


def is_offered(profile: ChatProfile, device: str) -> bool:
    """Whether this screen is offered the profile in the switcher.

    Listed *and* meant for the device -- the pair the client counts
    (``offeredProfiles`` in ``ChatProfiles.tsx``) to decide whether to draw a
    switcher at all. Not "visible": a thread resumed into an unlisted profile
    still names it in the trigger, and that one never counts towards the
    decision.
    """
    return is_listed(profile) and matches_device(
        getattr(profile, "device", "all"), device
    )


def pick_default_profile(profiles: Sequence[ChatProfile], device: str) -> Optional[str]:
    """Which profile a fresh chat opens in on this screen, by name.

    ``pickDefaultProfile`` in ``use-mobile.tsx``, fallback for fallback. An
    unlisted profile is a door somebody else opens -- a starter, a handoff, a
    resumed thread -- so it is never chosen here even when it carries
    ``default`` and even when it is the only one this device matches. The
    last two fallbacks ignore first the device and then ``listed``, because a
    configuration in which everything is filtered out must still yield a
    name: without one the client never opens the socket.
    """
    listed = [p for p in profiles if is_listed(p)]
    visible = [p for p in listed if matches_device(getattr(p, "device", "all"), device)]
    chosen = next(
        (p for p in visible if p.default),
        next(iter(visible), next(iter(listed), next(iter(profiles), None))),
    )
    return chosen.name if chosen is not None else None


def check_one_default_per_device(profiles: Sequence[ChatProfile]) -> None:
    """Refuse a profile list that lands somebody nowhere. Raises ``ValueError``.

    The client picks the first offered ``default`` and says nothing about the
    rest, so two defaults for one screen class is a silent coin toss and none
    is a silent first-in-the-list -- either way a person arrives somewhere
    they were not sent and cannot tell why. This is the rule read off the
    client's own default-picking, so a list that passes here opens where the
    application said it would.

    ``default=True`` with ``listed=False`` is rejected first and separately:
    it names a landing place the picker skips by design, which is a mistake
    about what ``listed`` means rather than a miscount.

    Call it where the list is built -- at import, next to the literal -- so
    the failure is a start-up error rather than a blank page.
    """
    doors = [p.name for p in profiles if p.default and not is_listed(p)]
    if doors:
        raise ValueError(
            f"Chat profiles with default=True and listed=False: {', '.join(doors)}. "
            "A door is not a landing place: an unlisted profile is skipped when "
            "the default is picked, so nothing would ever open in it."
        )
    for device in DEVICE_CLASSES:
        defaults = [p.name for p in profiles if p.default and is_offered(p, device)]
        if len(defaults) != 1:
            raise ValueError(
                f"Chat profiles with default=True offered on {device!r}: "
                f"{len(defaults)} ({', '.join(defaults) or 'none'}); exactly one "
                "is needed, or the client picks for you and says nothing."
            )


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
