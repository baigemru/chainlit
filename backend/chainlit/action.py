"""A button the application attaches to a message.

A plain dataclass: the wire shape is ``chainlit.protocol.payloads.Action``
and the emitter converts ``to_dict()`` into it, so the class only has to
produce that dict. ``forId`` is spelled the way the wire spells it because
the runner rebuilds an ``Action`` straight from the clicked payload.
"""

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Literal, Mapping, Optional

from chainlit.context import context


@dataclass
class Action:
    # Name of the action, this should be used in the action_callback
    name: str
    # The parameters to call this action with. Defaults to empty because the
    # wire drops an empty payload (omit_defaults) and the click posts back
    # exactly what it received: a chip with nothing to say used to arrive as
    # a missing argument and a 500.
    payload: Dict = field(default_factory=dict)
    # The label of the action. This is what the user will see.
    label: str = ""
    # The tooltip of the action button. This is what the user will see when they hover the action.
    tooltip: str = ""
    # The lucid icon name for this action.
    icon: Optional[str] = None
    # How the button asks to be drawn: "primary" for the one thing to do
    # next, "secondary" for an alternative to it, "chip" for a small pill
    # carrying a question, "default" for the quiet row. Nothing here is a
    # colour -- the palette belongs to the client. An unknown value is
    # refused by the wire struct when the action is sent, not here.
    variant: Literal["default", "primary", "secondary", "chip"] = "default"
    # This should not be set manually, only used internally.
    forId: Optional[str] = None
    # The ID of the action
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, action: Mapping[str, Any]) -> "Action":
        return cls(**{k: v for k, v in action.items() if k in _FIELDS})

    async def send(self, for_id: str) -> None:
        self.forId = for_id
        context.emitter.add_action(self.to_dict())

    async def remove(self) -> None:
        context.emitter.remove_action(self.id)


_FIELDS = frozenset(Action.__dataclass_fields__)
