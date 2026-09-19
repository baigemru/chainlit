"""The account page, minus everything that knows about HTTP.

An application declares **one** ``msgspec.Struct`` and the engine derives the
rest from it: ``msgspec.json.schema`` is the form the client renders,
``msgspec.convert`` is the validator a save runs through, ``msgspec.to_builtins``
is what goes into the database, and the same type documents the route. Nothing
is mirrored by hand, because a second description of a shape is a second thing
to keep in step with the first -- which is what the retired widget layer
(``input_widget.py``, ``InputWidgetSpec``) was, and why it is gone.

This module imports no Litestar and no configuration, so the rules below can be
exercised without a request and without a process-global. The route is
``chainlit.controllers.account``.
"""

from typing import Any, Dict, Mapping, Optional, Type, TypeVar

import msgspec
from msgspec import inspect as msgspec_inspect, structs

from chainlit.logger import logger

__all__ = (
    "AccountPage",
    "as_account",
    "build_page",
    "decode_stored",
    "register",
    "schema_of",
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


def as_account(value: Any, cls: Type[S]) -> S:
    """What an ``on_account_load`` return means.

    Strict on purpose. A hook that returns a shape its own Struct refuses is
    an application bug, and the msgspec message names the field; the lenient
    path below is for values that were stored under an older version of the
    Struct, which is a different thing entirely.
    """
    if isinstance(value, cls):
        return value
    return msgspec.convert(value, cls)


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
