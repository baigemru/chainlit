"""Every tag and every field name in the table belongs to the protocol.

The boundary test guards the table's *imports* and its socket.io *event
names*. Neither can see a field name, and that is where the table drifted:
a row spelled against the payload the legacy driver happens to build is
green forever and can never match a real frame, because the driver is the
only thing that ever produces that shape.

So this asserts the other half. A tag has to name a branch of ``ServerMsg``
or ``ClientMsg``, and every field the row reads has to be a field of that
branch, under the name the wire actually uses. Test modules are exempt from
the import ban -- checking the table against the protocol is the one thing
that requires seeing both.

The matching check of the driver's own translation tables left with the
socket.io driver; a driver that speaks the protocol natively has nothing
to translate.
"""

from typing import Any, Dict, List, Tuple

import msgspec

from chainlit.protocol.client import ClientMsg
from chainlit.protocol.server import ServerMsg
from tests.socketspec.cases import SCENARIOS


def _fields_by_tag(union: Any) -> Dict[str, Tuple[str, ...]]:
    """Tag -> the field names that tag's branch puts on the wire."""
    return {
        branch.__struct_config__.tag: tuple(
            field.encode_name for field in msgspec.structs.fields(branch)
        )
        for branch in union.__args__
    }


SERVER = _fields_by_tag(ServerMsg)
CLIENT = _fields_by_tag(ClientMsg)


def _check(
    known: Dict[str, Tuple[str, ...]],
    tag: str,
    paths: Any,
    where: str,
    direction: str,
) -> List[str]:
    """Every way this row disagrees with the protocol, not just the first."""
    if tag not in known:
        return [
            (
                f"{where}: {tag!r} is not a {direction} tag. The table is "
                f"written in the protocol, so a tag no branch carries is "
                f"either a typo or a name borrowed from the transport."
            )
        ]
    problems = []
    for path in paths:
        # Only the head is resolved: it is the field the frame carries, and
        # anything deeper belongs to a payload struct the row is free to
        # reach into. The head is where every drift found so far lived.
        head = path.split(".")[0]
        if head not in known[tag]:
            problems.append(
                f"{where}: {tag!r} has no field {head!r}; it carries "
                f"{list(known[tag])}."
            )
    return problems


def test_every_row_speaks_the_protocol():
    """Reported all at once: a drift is usually a whole vocabulary, not a row."""
    problems: List[str] = []
    for scenario in SCENARIOS:
        for incoming in scenario.when:
            problems += _check(
                CLIENT,
                incoming.tag,
                incoming.payload.keys(),
                f"{scenario.name!r} when",
                "client",
            )
        for expectation in scenario.expect:
            problems += _check(
                SERVER,
                expectation.tag,
                expectation.where.keys(),
                f"{scenario.name!r} expect",
                "server",
            )
        for tag in scenario.forbid:
            problems += _check(SERVER, tag, (), f"{scenario.name!r} forbid", "server")

    assert not problems, "\n".join(
        ["the table does not speak the protocol:", *sorted(set(problems))]
    )
