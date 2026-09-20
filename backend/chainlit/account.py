"""The account page, minus everything that knows about HTTP.

An application declares **one** ``msgspec.Struct`` and the engine derives the
rest from it: ``msgspec.json.schema`` is the form the client renders,
``msgspec.convert`` is the validator a save runs through, ``msgspec.to_builtins``
is what goes into the database, and the same type documents the route. Nothing
is mirrored by hand, because a second description of a shape is a second thing
to keep in step with the first -- which is what the retired widget layer
(``input_widget.py``, ``InputWidgetSpec``) was, and why it is gone.

The buttons an application puts on the page (``x-actions`` in the schema,
``@cl.account_action`` in the code) follow the same rule: the *schema* says
which Struct the item under a button is, so the item the client sends back is
converted through that and the hook is handed a typed object. An annotation on
the hook would be a second declaration of the same thing, free to disagree
with the form the user was looking at.

The same rule puts sections in the navigation: ``x-pinned: true`` in a
section field's ``Meta(extra_json_schema=...)`` marks it for the pinned block
of the left panel, next to ``x-icon`` for the glyph and ``x-actions`` for the
buttons. It is a hint carried in the schema rather than a list kept in
``config.toml`` because the sections *are* the Struct's fields: a second list
of their names would go stale the first time one was renamed. The engine only
carries it -- the form is served with the settings (``ui.account.schema``) as
well as with the page, and what the client does with a pinned section, and
how many of them it has room for, is the client's and the application's
business.

One field of the schema is not the application's to send back: a leaf marked
``readOnly`` is derived -- a rate, a balance, a quota -- and ``strip_readonly``
puts every one of them back to its default before anything is stored. The
engine owns that rule so no application has to keep a list of which of its own
fields are computed and clean them out of each save by hand.

This module imports no Litestar and no configuration, so the rules below can be
exercised without a request and without a process-global. The route is
``chainlit.controllers.account``.
"""

import inspect
from typing import Any, Dict, Mapping, Optional, Type, TypeVar, Union

import msgspec
from msgspec import inspect as msgspec_inspect, structs

from chainlit.logger import logger

__all__ = (
    "AccountActionCall",
    "AccountActionResponse",
    "AccountActionResult",
    "AccountPage",
    "OpenThread",
    "OpenThreadOutcome",
    "PageOutcome",
    "Refresh",
    "Toast",
    "ToastOutcome",
    "as_account",
    "build_page",
    "call_hook",
    "decode_stored",
    "element_type_at",
    "register",
    "schema_of",
    "strip_readonly",
)

S = TypeVar("S", bound=msgspec.Struct)


class AccountPage(msgspec.Struct):
    """Everything the page needs in one answer.

    The schema travels with the values on purpose: an application can change
    its Struct between two page loads, and a client that had cached the form
    separately would then render yesterday's fields over today's values.
    """

    schema: Dict[str, Any]
    values: Dict[str, Any]
    readonly: bool
    message: Optional[str] = None


# --------------------------------------------------------------------------
# Actions: what a button on the page may ask for, and what it answers
# --------------------------------------------------------------------------


class Toast(msgspec.Struct, tag_field="t", tag="toast"):
    """Say something and leave the page as it is."""

    message: str


class Refresh(msgspec.Struct, tag_field="t", tag="refresh"):
    """Rebuild the page, optionally storing what the action decided.

    ``account`` is an instance of the registered Struct. Given one, the
    engine stores it -- ``strip_readonly`` first -- with the request's own
    session and then draws the page the way a ``GET`` draws it, load hook
    and all. That is the whole reason it is here: an action that dismissed
    a notice used to have to open its own session and commit behind the
    request, and the page the request went on to build then showed the
    value from *before* the dismissal.

    ``None`` means the action changed nothing of its own -- the page is
    still rebuilt, because the load hook may have something new to say.
    """

    message: Optional[str] = None
    account: Any = None


class OpenThread(msgspec.Struct, tag_field="t", tag="open_thread"):
    """Take the user into a new conversation, carrying something along.

    Exactly the handover a profile switch performs, reached from a page
    instead of from a session: the engine mints the successor's thread,
    parks ``transit_message`` under it, and the browser opens it. The value
    never travels through the client -- ``has_transit_message`` is all it is
    told -- so it may be anything the application's ``on_chat_start`` can
    read back out of ``cl.user_session``.
    """

    chat_profile: str
    transit_message: Any = None
    parent: Optional[str] = None


AccountActionResult = Union[Toast, Refresh, OpenThread]
"""What an ``@cl.account_action`` hook may return. Anything else is a bug in
the application and is reported as one."""


# The toast needs no translation: what the application returns is already
# what the client is told, down to the tag. A second Struct of the same
# shape would only be a second thing to keep in step with this one.
ToastOutcome = Toast


class PageOutcome(msgspec.Struct, tag_field="t", tag="page"):
    """The rebuilt page, for a client that must not reload to see it."""

    page: AccountPage
    message: Optional[str] = None


class OpenThreadOutcome(msgspec.Struct, tag_field="t", tag="open_thread"):
    """The thread the engine minted, and what the client does with it.

    ``transit_message`` is deliberately absent: the record is parked
    server-side under ``thread_id`` and claimed by the socket that arrives
    for it, so the browser carries a thread id and a boolean, never the
    payload. ``thread_id`` is ``None`` when there was nothing to park -- no
    message and no parent -- and the client then switches profile without a
    successor thread, exactly as a ``session.handoff`` with no
    ``next_thread_id`` does. Minting an id for an empty handover would put an
    empty conversation in the address bar; the one function that mints,
    ``mint_handover``, already refuses to, and this outcome does not second-
    guess it.
    """

    thread_id: Optional[str]
    chat_profile: str
    has_transit_message: bool = False


class AccountActionResponse(msgspec.Struct):
    """The one shape ``POST /project/account/actions/{name}`` answers with.

    A tagged union rather than one Struct with three optional fields: three
    fields would let a response mean two things at once, and the client
    would need a rule about which wins. ``t`` decides, and a switch over it
    has no fourth case.
    """

    outcome: Union[ToastOutcome, PageOutcome, OpenThreadOutcome]


class AccountActionCall(msgspec.Struct):
    """What the client sends when a button on the page is pressed.

    ``path`` is the dotted address of the thing the button sits on --
    ``watch.items.3`` for a card, ``watch`` for a tab -- and it is what the
    element's type is resolved from. ``item`` is the card as the form holds
    it, or ``None`` for a tab action.
    """

    path: str
    item: Any = None


def element_type_at(cls: Type[msgspec.Struct], path: str) -> Type[msgspec.Struct]:
    """The Struct a dotted ``path`` addresses inside ``cls``.

    Resolved from the registered Struct, never from the hook's annotation:
    the schema is what the client drew the button from, so the schema is
    what decides which type the item it sends back is. An annotation would
    be a second declaration of that, free to disagree.

    Raises:
        LookupError: naming the segment that does not resolve, or saying
            that the path ends somewhere that is not a Struct.
    """
    node: msgspec_inspect.Type = msgspec_inspect.type_info(cls)
    walked = ""
    for segment in path.split("."):
        node = _step(node, segment, walked or cls.__name__)
        walked = f"{walked}.{segment}" if walked else segment
    node = _only_type(node)
    if not isinstance(node, msgspec_inspect.StructType):
        raise LookupError(
            f"{path!r} names a {type(node).__name__}, not a Struct. An action "
            "belongs to a card or to a tab, and both are Structs."
        )
    return node.cls


def _step(node: msgspec_inspect.Type, segment: str, where: str) -> msgspec_inspect.Type:
    """One segment of a path: a field by name, or a list item by index."""
    node = _only_type(node)
    if isinstance(node, msgspec_inspect.StructType):
        for field in node.fields:
            if field.encode_name == segment:
                return field.type
        raise LookupError(f"{where} has no field {segment!r}")
    if isinstance(node, msgspec_inspect.ListType) and segment.isdigit():
        return node.item_type
    raise LookupError(
        f"{where} cannot be addressed by {segment!r}: it is a {type(node).__name__}."
    )


def _only_type(node: msgspec_inspect.Type) -> msgspec_inspect.Type:
    """Peel the wrappers that carry no address of their own.

    ``Annotated[...]`` becomes ``Metadata`` and ``X | None`` a ``UnionType``,
    and neither is a step a path can name -- a form field is addressed by
    its name whether or not it is nullable. Peeled through ``_nested_types``,
    which is the one place that knows what each wrapper holds; a wrapper
    that holds more than one candidate (a genuine union of two Structs) is
    left alone, and the caller reports it as unaddressable.
    """
    while not isinstance(node, (msgspec_inspect.StructType, msgspec_inspect.ListType)):
        candidates = [
            nested
            for nested in _nested_types(node)
            if not isinstance(nested, msgspec_inspect.NoneType)
        ]
        if len(candidates) != 1:
            return node
        node = candidates[0]
    return node


async def call_hook(hook: Any, *args: Any) -> Any:
    """Run an application hook, sync or async, and let it raise.

    Not ``wrap_user_function``: that wrapper logs an exception and answers
    ``None``, which the session hooks want and the account hooks do not --
    a load hook that crashed would silently fall back to somebody's old
    data, a save hook that crashed would be followed by a store and a
    "saved", and an action that crashed would answer "done". Litestar's
    exception handling turns the raise into the 500 it is.
    """
    result = hook(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def register(cls: Type[S]) -> Type[S]:
    """Check what ``@cl.account`` was handed, and hand it back.

    Both rules exist so the page can be drawn for somebody who has never saved
    anything. A missing default means ``cls()`` raises, and there would be no
    form to show a first-time visitor -- and no baseline to drop a stale stored
    field back to.
    """
    if not (isinstance(cls, type) and issubclass(cls, msgspec.Struct)):
        raise TypeError(
            f"@cl.account takes a msgspec.Struct subclass, got {cls!r}. The "
            "Struct is the schema, the validator and the storage shape at once."
        )
    _assert_every_field_has_a_default(
        msgspec_inspect.type_info(cls), cls.__name__, set()
    )
    return cls


def schema_of(cls: Type[msgspec.Struct]) -> Dict[str, Any]:
    """The JSON Schema the client builds the form from."""
    return msgspec.json.schema(cls)


def build_page(
    account: msgspec.Struct,
    *,
    readonly: bool,
    message: Optional[str] = None,
) -> AccountPage:
    """The response both routes answer with, built from one instance."""
    return AccountPage(
        schema=schema_of(type(account)),
        values=msgspec.to_builtins(account),
        readonly=readonly,
        message=message,
    )


def as_account(value: Any, cls: Type[S], *, hook: str) -> S:
    """An instance of the registered Struct, and nothing else.

    A mapping used to be converted here, and while the return only fed the
    page that was harmless. It is not any more: the return is *also* what
    gets stored, and ``msgspec.convert`` fills every key a mapping leaves out
    with the field's default -- so a hook that meant to change one thing
    would write the defaults over everything the user had ever saved. The
    hook is handed the current values precisely so that it does not have to
    describe them again: ``msgspec.structs.replace(account, plan="pro")``.

    ``None`` is refused for the same reason it is no longer "use the stored
    values": the hook is *handed* those, so the fallback has nothing left to
    mean and a missing ``return`` would blank the page it was to fill.
    """
    if isinstance(value, cls):
        return value
    if value is None:
        raise TypeError(
            f"{hook} returned None. It is handed the stored {cls.__name__} and "
            "must return the one to show -- returning it unchanged is "
            "`return account`."
        )
    raise TypeError(
        f"{hook} must return a {cls.__name__}, not {type(value).__name__}. What "
        "it returns is stored as well as shown, and a mapping would store the "
        "default of every key it leaves out -- change what you were handed with "
        "msgspec.structs.replace(account, ...) instead."
    )


def strip_readonly(value: S) -> S:
    """The same values with every ``readOnly`` leaf back at its default.

    A ``readOnly`` field is *derived* -- a rate, a balance, a quota -- and
    the application recomputes it on every load. Storing it would put a copy
    of a computed number in the database whose only job is to be stale, and
    a ``PUT`` would store whatever the browser happened to send for it. So
    the engine never stores one: this runs before every write, on the way in
    from a ``PUT``, on the way out of ``on_account_load`` and on a
    ``Refresh``'s ``account``.

    Reset to the field's default, not carried over from the stored value: a
    card in a ``list[Struct]`` has no stored twin to carry anything from --
    the list is rewritten whole, and an element's position is not an identity
    -- so a per-field rule that needed one would have to be a second, weaker
    rule for lists. The default is what ``register`` already guarantees every
    field has, and it is also what a row written under this rule holds, so
    the two sides of the "has anything changed" comparison agree.

    ``readOnly`` on a nested Struct field resets that whole subtree; the walk
    goes into plain Struct fields and into the Struct elements of a list, which
    are the two shapes the page is built from.
    """
    node = msgspec_inspect.type_info(type(value))
    if not isinstance(node, msgspec_inspect.StructType):  # pragma: no cover - defensive
        return value
    # One blank instance per Struct rather than reading ``Field.default`` and
    # ``Field.default_factory``: ``register`` guarantees ``cls()`` works, and
    # that guarantee is the one thing this needs.
    blank = type(value)()
    changes: Dict[str, Any] = {}
    for field in node.fields:
        current = getattr(value, field.name)
        if _is_readonly(field.type):
            default = getattr(blank, field.name)
            if current != default:
                changes[field.name] = default
            continue
        rebuilt = _strip_within(current)
        if rebuilt is not current:
            changes[field.name] = rebuilt
    if not changes:
        return value
    return structs.replace(value, **changes)


def _strip_within(current: Any) -> Any:
    """``strip_readonly`` through whatever holds a Struct.

    Returns the argument itself when nothing changed, so the caller can tell
    an untouched field by identity and leave the instance alone.
    """
    if isinstance(current, msgspec.Struct):
        return strip_readonly(current)
    if isinstance(current, list):
        stripped = [_strip_within(item) for item in current]
        if all(new is old for new, old in zip(stripped, current)):
            return current
        return stripped
    return current


def _is_readonly(node: msgspec_inspect.Type) -> bool:
    """Whether ``readOnly`` is what the field's schema would say.

    Read off ``Metadata.extra_json_schema``, which is where
    ``Meta(extra_json_schema={"readOnly": True})`` lands and the one thing
    ``msgspec.json.schema`` copies into the document the client renders. The
    loop only peels ``Metadata``: ``readOnly`` deeper inside -- on a list's
    item type, say -- describes that type, not this field.
    """
    while isinstance(node, msgspec_inspect.Metadata):
        if (node.extra_json_schema or {}).get("readOnly"):
            return True
        node = node.type
    return False


def decode_stored(stored: Any, cls: Type[S]) -> S:
    """The stored values as ``cls``, dropping whatever no longer fits.

    Field by field, starting from the defaults, rather than one
    ``msgspec.convert`` over the whole document: an application renames a
    field or retypes it, and a whole-object decode would then refuse the
    stored object and take the page down for every user who saved under the
    old shape. A key that is gone, or a value that no longer converts, is
    dropped with a warning naming it; the rest is kept.
    """
    applied: Dict[str, Any] = {}
    if isinstance(stored, Mapping):
        by_encode_name = {field.encode_name: field for field in structs.fields(cls)}
        for key, value in stored.items():
            field = by_encode_name.get(key)
            if field is None:
                logger.warning(
                    "account: dropping stored field %r, which %s no longer declares",
                    key,
                    cls.__name__,
                )
                continue
            try:
                applied[field.name] = msgspec.convert(value, field.type)
            except msgspec.ValidationError as error:
                logger.warning(
                    "account: dropping stored field %r of %s: %s",
                    key,
                    cls.__name__,
                    error,
                )
    return structs.replace(cls(), **applied)


def _assert_every_field_has_a_default(
    node: msgspec_inspect.Type, path: str, seen: set[int]
) -> None:
    """Walk msgspec's own view of the type and refuse a required field.

    ``msgspec.inspect`` is walked rather than the annotations: it has already
    unwrapped ``Annotated``, ``X | None``, ``list[X]`` and the rest, so this
    is one rule applied once instead of a re-implementation of msgspec's type
    resolution that would drift from it.
    """
    if id(node) in seen:
        return
    seen.add(id(node))

    if isinstance(node, msgspec_inspect.StructType):
        for field in node.fields:
            where = f"{path}.{field.encode_name}"
            if field.required:
                raise TypeError(
                    f"@cl.account: {where} has no default. Every field needs "
                    "one -- the page has to render for a user who has never "
                    "saved, and a stored value that no longer fits is dropped "
                    "back to the default field by field. A nested Struct takes "
                    "msgspec.field(default_factory=...)."
                )
            _assert_every_field_has_a_default(field.type, where, seen)
        return

    for nested in _nested_types(node):
        _assert_every_field_has_a_default(nested, path, seen)


def _nested_types(node: msgspec_inspect.Type) -> Any:
    """The types a wrapper type holds, whatever the wrapper is called.

    Read off the wrapper's own fields instead of a table of
    ``Metadata.type`` / ``ListType.item_type`` / ``UnionType.types``: every
    ``inspect.Type`` is a Struct, so this keeps working when msgspec adds one.
    """
    for field in structs.fields(node):
        value = getattr(node, field.name)
        if isinstance(value, msgspec_inspect.Type):
            yield value
        elif isinstance(value, tuple):
            yield from (
                item for item in value if isinstance(item, msgspec_inspect.Type)
            )
