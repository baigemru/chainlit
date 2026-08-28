"""The messages of the current conversation, as ``cl.chat_context`` sees them.

Kept on the session's own state dict rather than in a module-level table
keyed by session id: the session is what lives and dies, and a table that
outlives it is a leak that has to be swept by whoever tears the session
down. The key is private so it is not confused with what the application
stores through ``cl.user_session``.
"""

from typing import TYPE_CHECKING, List

from chainlit.context import context

if TYPE_CHECKING:
    from chainlit.message import Message

MESSAGES_KEY = "__chat_messages"


class ChatContext:
    def _messages(self) -> List["Message"]:
        return context.session.state.setdefault(MESSAGES_KEY, [])

    def get(self) -> List["Message"]:
        if not context.session:
            return []
        return self._messages().copy()

    def add(self, message: "Message"):
        if not context.session:
            return

        messages = self._messages()
        if message not in messages:
            messages.append(message)

        return message

    def remove(self, message: "Message") -> bool:
        if not context.session:
            return False

        messages = self._messages()
        if message in messages:
            messages.remove(message)
            return True

        return False

    def clear(self) -> None:
        if context.session:
            context.session.state[MESSAGES_KEY] = []

    def to_openai(self):
        messages = []
        for message in self.get():
            if message.type == "assistant_message":
                messages.append({"role": "assistant", "content": message.content})
            elif message.type == "user_message":
                messages.append({"role": "user", "content": message.content})
            else:
                messages.append({"role": "system", "content": message.content})

        return messages


chat_context = ChatContext()
