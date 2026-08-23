import asyncio
import json
import math
import time
import uuid
from abc import ABC
from typing import Dict, List, Literal, Optional, Union, cast

from literalai.observability.step import MessageStepType

from chainlit.action import Action
from chainlit.chat_context import chat_context
from chainlit.config import config
from chainlit.context import context, local_steps
from chainlit.data import get_data_layer
from chainlit.element import CustomElement, ElementBased
from chainlit.logger import logger
from chainlit.resume_policy import (
    RESUME_POLICY_DELETE,
    RESUME_POLICY_KEEP,
    RESUME_POLICY_KEY,
)
from chainlit.step import StepDict, WaitDict
from chainlit.types import (
    AskActionResponse,
    AskActionSpec,
    AskElementResponse,
    AskElementSpec,
    AskFileResponse,
    AskFileSpec,
    AskSpec,
    FileDict,
)
from chainlit.utils import utc_now


class MessageBase(ABC):
    id: str
    thread_id: str
    author: str
    content: str = ""
    type: MessageStepType = "assistant_message"
    streaming = False
    created_at: Union[str, None] = None
    fail_on_persist_error: bool = False
    persisted = False
    is_error = False
    command: Optional[str] = None
    modes: Optional[Dict[str, str]] = None
    parent_id: Optional[str] = None
    language: Optional[str] = None
    metadata: Optional[Dict] = None
    tags: Optional[List[str]] = None
    wait_for_answer = False
    # Client-side waiting presentation mode (shimmer + text rotation).
    # Transient: emitted over the socket only, never persisted.
    wait: Union[bool, List[str]] = False
    wait_interval: float = 5.0
    wait_loop: bool = False

    def __post_init__(self) -> None:
        self.thread_id = context.session.thread_id

        previous_steps = local_steps.get() or []
        parent_step = previous_steps[-1] if previous_steps else None
        if parent_step:
            self.parent_id = parent_step.id

        if not getattr(self, "id", None):
            self.id = str(uuid.uuid4())

    def _apply_resume_policy(self, resume: str) -> None:
        """Record the resume policy in the step metadata.

        ``"keep"`` (the default) is a strict no-op: the metadata is left
        untouched. ``"delete"`` marks the step as not surviving a thread
        resume of a dead session — see ``chainlit.resume_policy``. Written
        into ``self.metadata`` at construction time so every persist path
        (``_create``, ``update``, favorite toggles) carries the flag.
        """
        if resume == RESUME_POLICY_KEEP:
            return
        if resume != RESUME_POLICY_DELETE:
            raise ValueError(
                f'resume must be "{RESUME_POLICY_KEEP}" or '
                f'"{RESUME_POLICY_DELETE}", got {resume!r}'
            )
        self.metadata = {
            **(self.metadata or {}),
            RESUME_POLICY_KEY: RESUME_POLICY_DELETE,
        }

    @classmethod
    def from_dict(self, _dict: StepDict):
        type = _dict.get("type", "assistant_message")
        return Message(
            id=_dict["id"],
            parent_id=_dict.get("parentId"),
            created_at=_dict["createdAt"],
            content=_dict["output"],
            author=_dict.get("name", config.ui.name),
            command=_dict.get("command"),
            modes=_dict.get("modes"),
            type=type,  # type: ignore
            language=_dict.get("language"),
            metadata=_dict.get("metadata", {}),
        )

    def to_dict(self) -> StepDict:
        _dict: StepDict = {
            "id": self.id,
            "threadId": self.thread_id,
            "parentId": self.parent_id,
            "createdAt": self.created_at,
            "command": self.command,
            "modes": self.modes,
            "start": self.created_at,
            "end": self.created_at,
            "output": self.content,
            "name": self.author,
            "type": self.type,
            "language": self.language,
            "streaming": self.streaming,
            "isError": self.is_error,
            "waitForAnswer": self.wait_for_answer,
            "metadata": self.metadata or {},
            "tags": self.tags,
        }

        return _dict

    def _wait_payload(self) -> Optional[WaitDict]:
        """Normalize the transient `wait` presentation payload.

        Returns None when wait mode is off. Never included in `to_dict()` so
        it can not leak into the data layer — callers attach it to a copy of
        the step dict that goes to the emitter only.
        """
        # An empty texts list means "shimmer only", same as wait=True.
        if not self.wait and not isinstance(self.wait, list):
            return None

        texts = list(self.wait) if isinstance(self.wait, list) else []

        # A presentation hint must never break send()/update(): fall back to
        # the default interval if the value is not a finite number.
        try:
            interval = float(self.wait_interval)
        except (TypeError, ValueError):
            interval = 5.0
        if not math.isfinite(interval):
            interval = 5.0

        interval_ms = round(max(interval, 2.0) * 1000)

        return {
            "texts": texts,
            "intervalMs": interval_ms,
            "loop": bool(self.wait_loop),
        }

    async def update(
        self,
    ):
        """
        Update a message already sent to the UI.
        """

        if self.streaming:
            self.streaming = False

        step_dict = self.to_dict()
        chat_context.add(self)

        data_layer = get_data_layer()
        if data_layer:
            try:
                asyncio.create_task(data_layer.update_step(step_dict))
            except Exception as e:
                if self.fail_on_persist_error:
                    raise e
                logger.error(f"Failed to persist message update: {e!s}")

        wait_payload = self._wait_payload()
        if wait_payload is not None:
            # Transient field for the emitter only; the data layer above got
            # the original dict without it. Consumed on emit.
            await context.emitter.update_step({**step_dict, "wait": wait_payload})
            self.wait = False
        else:
            await context.emitter.update_step(step_dict)

        return True

    async def remove(self):
        """
        Remove a message already sent to the UI.
        """
        chat_context.remove(self)
        step_dict = self.to_dict()
        data_layer = get_data_layer()
        if data_layer:
            try:
                asyncio.create_task(data_layer.delete_step(step_dict["id"]))
            except Exception as e:
                if self.fail_on_persist_error:
                    raise e
                logger.error(f"Failed to persist message deletion: {e!s}")

        await context.emitter.delete_step(step_dict)

        return True

    async def _create(self):
        step_dict = self.to_dict()
        data_layer = get_data_layer()
        if data_layer and not self.persisted:
            try:
                asyncio.create_task(data_layer.create_step(step_dict))
                self.persisted = True
            except Exception as e:
                if self.fail_on_persist_error:
                    raise e
                logger.error(f"Failed to persist message creation: {e!s}")

        return step_dict

    async def send(self):
        if not self.created_at:
            self.created_at = utc_now()
        if self.content is None:
            self.content = ""

        if config.code.author_rename:
            self.author = await config.code.author_rename(self.author)

        if self.streaming:
            self.streaming = False

        step_dict = await self._create()
        chat_context.add(self)

        wait_payload = self._wait_payload()
        if wait_payload is not None:
            # Transient field for the emitter only; the data layer (via
            # _create) got the original dict without it. Consumed on emit.
            await context.emitter.send_step({**step_dict, "wait": wait_payload})
            self.wait = False
        else:
            await context.emitter.send_step(step_dict)

        return self

    async def stream_token(self, token: str, is_sequence=False):
        """
        Sends a token to the UI. This is useful for streaming messages.
        Once all tokens have been streamed, call .send() to end the stream and persist the message if persistence is enabled.
        """
        if not token:
            return

        if is_sequence:
            self.content = token
        else:
            self.content += token

        assert self.id

        if not self.streaming:
            self.streaming = True
            step_dict = self.to_dict()
            await context.emitter.stream_start(step_dict)
        else:
            await context.emitter.send_token(
                id=self.id, token=token, is_sequence=is_sequence
            )


class Message(MessageBase):
    """
    Send a message to the UI

    Args:
        content (Union[str, Dict]): The content of the message.
        author (str, optional): The author of the message, this will be used in the UI. Defaults to the assistant name (see config).
        language (str, optional): Language of the code is the content is code. See https://react-code-blocks-rajinwonderland.vercel.app/?path=/story/codeblock--supported-languages for a list of supported languages.
        actions (List[Action], optional): A list of actions to send with the message.
        elements (List[ElementBased], optional): A list of elements to send with the message.
        wait (Union[bool, List[str]], optional): Client-side waiting presentation mode. False (default) — regular message. True — shimmer over the content, no text rotation. List of strings — shimmer plus client-side rotation of these texts. Transient: not persisted, consumed on the next send()/update().
        wait_interval (float, optional): Seconds between text rotations (minimum 2). Defaults to 5.
        wait_loop (bool, optional): Whether the rotation loops back to the first text after the last one (True) or holds the last text (False, default).
        resume (Literal["keep", "delete"], optional): Whether the message survives a thread resume of a dead session. "keep" (default) — regular behavior. "delete" — on resume the message is hidden and deleted from the data layer together with its elements.
    """

    def __init__(
        self,
        content: Union[str, Dict],
        author: Optional[str] = None,
        language: Optional[str] = None,
        actions: Optional[List[Action]] = None,
        elements: Optional[List[ElementBased]] = None,
        type: MessageStepType = "assistant_message",
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        id: Optional[str] = None,
        parent_id: Optional[str] = None,
        command: Optional[str] = None,
        modes: Optional[Dict[str, str]] = None,
        created_at: Union[str, None] = None,
        wait: Union[bool, List[str]] = False,
        wait_interval: float = 5.0,
        wait_loop: bool = False,
        resume: Literal["keep", "delete"] = "keep",
    ):
        time.sleep(0.001)
        self.language = language
        if isinstance(content, dict):
            try:
                self.content = json.dumps(content, indent=4, ensure_ascii=False)
                self.language = "json"
            except TypeError:
                self.content = str(content)
                self.language = "text"
        elif isinstance(content, str):
            self.content = content
        else:
            self.content = str(content)
            self.language = "text"

        if id:
            self.id = str(id)

        if parent_id:
            self.parent_id = str(parent_id)

        if command:
            self.command = str(command)

        if modes:
            self.modes = modes

        if created_at:
            self.created_at = created_at

        self.metadata = metadata
        self.tags = tags

        self.author = author or config.ui.name
        self.type = type
        self.actions = actions if actions is not None else []
        self.elements = elements if elements is not None else []

        self.wait = wait
        self.wait_interval = wait_interval
        self.wait_loop = wait_loop
        if not self.content and isinstance(wait, list) and wait:
            self.content = wait[0]

        self._apply_resume_policy(resume)

        super().__post_init__()

    async def send(self):
        """
        Send the message to the UI and persist it in the cloud if a project ID is configured.
        Return the ID of the message.
        """
        await super().send()

        # Create tasks for all actions and elements
        tasks = [action.send(for_id=self.id) for action in self.actions]
        tasks.extend(element.send(for_id=self.id) for element in self.elements)

        # Run all tasks concurrently
        await asyncio.gather(*tasks)

        return self

    async def update(self):
        """
        Send the message to the UI and persist it in the cloud if a project ID is configured.
        Return the ID of the message.
        """
        await super().update()

        # Update tasks for all actions and elements
        tasks = [
            action.send(for_id=self.id)
            for action in self.actions
            if action.forId is None
        ]
        tasks.extend(element.send(for_id=self.id) for element in self.elements)

        # Run all tasks concurrently
        await asyncio.gather(*tasks)

        return True

    async def remove_actions(self):
        for action in self.actions:
            await action.remove()


class ErrorMessage(MessageBase):
    """
    Send an error message to the UI
    If a project ID is configured, the message will be persisted in the cloud.

    Args:
        content (str): Text displayed above the upload button.
        author (str, optional): The author of the message, this will be used in the UI. Defaults to the assistant name (see config).
    """

    def __init__(
        self,
        content: str,
        author: str = config.ui.name,
        fail_on_persist_error: bool = False,
    ):
        self.content = content
        self.author = author
        self.type = "assistant_message"
        self.is_error = True
        self.fail_on_persist_error = fail_on_persist_error

        super().__post_init__()

    async def send(self):
        """
        Send the error message to the UI and persist it in the cloud if a project ID is configured.
        Return the ID of the message.
        """
        return await super().send()


class AskMessageBase(MessageBase):
    async def remove(self):
        removed = await super().remove()
        if removed:
            await context.emitter.clear("clear_ask")


class AskUserMessage(AskMessageBase):
    """
    Ask for the user input before continuing.
    If the user does not answer in time (see timeout), a TimeoutError will be raised or None will be returned depending on raise_on_timeout.
    If a project ID is configured, the message will be uploaded to the cloud storage.

    Args:
        content (str): The content of the prompt.
        author (str, optional): The author of the message, this will be used in the UI. Defaults to the assistant name (see config).
        timeout (int, optional): The number of seconds to wait for an answer before raising a TimeoutError.
        raise_on_timeout (bool, optional): Whether to raise a socketio TimeoutError if the user does not answer in time.
        resume (Literal["keep", "delete"], optional): "delete" — the step does not survive a thread resume of a dead session (a live pending ask is untouched). Defaults to "keep".
    """

    def __init__(
        self,
        content: str,
        author: str = config.ui.name,
        type: MessageStepType = "assistant_message",
        timeout: int = 60,
        raise_on_timeout: bool = False,
        resume: Literal["keep", "delete"] = "keep",
    ):
        self.content = content
        self.author = author
        self.timeout = timeout
        self.type = type
        self.raise_on_timeout = raise_on_timeout

        self._apply_resume_policy(resume)

        super().__post_init__()

    async def send(self) -> Union[StepDict, None]:
        """
        Sends the question to ask to the UI and waits for the reply.
        """
        if not self.created_at:
            self.created_at = utc_now()

        if config.code.author_rename:
            self.author = await config.code.author_rename(self.author)

        if self.streaming:
            self.streaming = False

        self.wait_for_answer = True

        step_dict = await self._create()

        spec = AskSpec(type="text", step_id=step_dict["id"], timeout=self.timeout)

        # In the transcript BEFORE the wait: a reconnect replay must show
        # the question above its answer, not below it.
        chat_context.add(cast("Message", self))

        res = cast(
            Union[None, StepDict],
            await context.emitter.send_ask_user(step_dict, spec, self.raise_on_timeout),
        )

        self.wait_for_answer = False

        return res


class AskFileMessage(AskMessageBase):
    """
    Ask the user to upload a file before continuing.
    If the user does not answer in time (see timeout), a TimeoutError will be raised or None will be returned depending on raise_on_timeout.
    If a project ID is configured, the file will be uploaded to the cloud storage.

    Args:
        content (str): Text displayed above the upload button.
        accept (Union[List[str], Dict[str, List[str]]]): List of mime type to accept like ["text/csv", "application/pdf"] or a dict like {"text/plain": [".txt", ".py"]}.
        max_size_mb (int, optional): Maximum size per file in MB. Maximum value is 100.
        max_files (int, optional): Maximum number of files to upload. Maximum value is 10.
        author (str, optional): The author of the message, this will be used in the UI. Defaults to the assistant name (see config).
        timeout (int, optional): The number of seconds to wait for an answer before raising a TimeoutError.
        raise_on_timeout (bool, optional): Whether to raise a socketio TimeoutError if the user does not answer in time.
        resume (Literal["keep", "delete"], optional): "delete" — the step does not survive a thread resume of a dead session (a live pending ask is untouched). Defaults to "keep".
    """

    def __init__(
        self,
        content: str,
        accept: Union[List[str], Dict[str, List[str]]],
        max_size_mb=2,
        max_files=1,
        author=config.ui.name,
        type: MessageStepType = "assistant_message",
        timeout=90,
        raise_on_timeout=False,
        resume: Literal["keep", "delete"] = "keep",
    ):
        self.content = content
        self.max_size_mb = max_size_mb
        self.max_files = max_files
        self.accept = accept
        self.type = type
        self.author = author
        self.timeout = timeout
        self.raise_on_timeout = raise_on_timeout

        self._apply_resume_policy(resume)

        super().__post_init__()

    async def send(self) -> Union[List[AskFileResponse], None]:
        """
        Sends the message to request a file from the user to the UI and waits for the reply.
        """
        if not self.created_at:
            self.created_at = utc_now()

        if self.streaming:
            self.streaming = False

        if config.code.author_rename:
            self.author = await config.code.author_rename(self.author)

        self.wait_for_answer = True

        step_dict = await self._create()

        spec = AskFileSpec(
            type="file",
            step_id=step_dict["id"],
            accept=self.accept,
            max_size_mb=self.max_size_mb,
            max_files=self.max_files,
            timeout=self.timeout,
        )

        # In the transcript BEFORE the wait: a reconnect replay must show
        # the question above its answer, not below it.
        chat_context.add(cast("Message", self))

        res = cast(
            Union[None, List[FileDict]],
            await context.emitter.send_ask_user(step_dict, spec, self.raise_on_timeout),
        )

        self.wait_for_answer = False

        if res:
            return [
                AskFileResponse(
                    id=r["id"],
                    name=r["name"],
                    path=str(r["path"]),
                    size=r["size"],
                    type=r["type"],
                )
                for r in res
            ]
        else:
            return None


class AskActionMessage(AskMessageBase):
    """
    Ask the user to select an action before continuing.
    If the user does not answer in time (see timeout), a TimeoutError will be raised or None will be returned depending on raise_on_timeout.
    """

    def __init__(
        self,
        content: str,
        actions: List[Action],
        author=config.ui.name,
        timeout=90,
        raise_on_timeout=False,
        resume: Literal["keep", "delete"] = "keep",
    ):
        self.content = content
        self.actions = actions
        self.author = author
        self.timeout = timeout
        self.raise_on_timeout = raise_on_timeout

        self._apply_resume_policy(resume)

        super().__post_init__()

    async def send(self) -> Union[AskActionResponse, None]:
        """
        Sends the question to ask to the UI and waits for the reply
        """
        if not self.created_at:
            self.created_at = utc_now()

        if self.streaming:
            self.streaming = False

        if config.code.author_rename:
            self.author = await config.code.author_rename(self.author)

        self.wait_for_answer = True

        step_dict = await self._create()

        action_keys = []

        for action in self.actions:
            action_keys.append(action.id)
            await action.send(for_id=str(step_dict["id"]))

        spec = AskActionSpec(
            type="action",
            step_id=step_dict["id"],
            timeout=self.timeout,
            keys=action_keys,
        )

        res = cast(
            Union[AskActionResponse, None],
            await context.emitter.send_ask_user(
                step_dict,
                spec,
                self.raise_on_timeout,
                # The client loses actions on refresh; they are re-emitted
                # alongside the ask on reconnect.
                restore_actions=[action.to_dict() for action in self.actions],
            ),
        )

        for action in self.actions:
            await action.remove()
        if res is None:
            self.content = "Timed out: no action was taken"
        else:
            self.content = f"**Selected:** {res['label']}"

        self.wait_for_answer = False

        await self.update()

        return res


class AskElementMessage(AskMessageBase):
    """Ask the user to submit a custom element."""

    def __init__(
        self,
        content: str,
        element: CustomElement,
        author=config.ui.name,
        timeout=90,
        raise_on_timeout=False,
        resume: Literal["keep", "delete"] = "keep",
    ):
        self.content = content
        self.element = element
        self.author = author
        self.timeout = timeout
        self.raise_on_timeout = raise_on_timeout

        self._apply_resume_policy(resume)

        super().__post_init__()

    async def send(self) -> Union[AskElementResponse, None]:
        """Send the custom element to the UI and wait for the reply."""
        if not self.created_at:
            self.created_at = utc_now()

        if self.streaming:
            self.streaming = False

        if config.code.author_rename:
            self.author = await config.code.author_rename(self.author)

        self.wait_for_answer = True

        step_dict = await self._create()

        await self.element.send(for_id=str(step_dict["id"]))

        spec = AskElementSpec(
            type="element",
            step_id=step_dict["id"],
            timeout=self.timeout,
            element_id=self.element.id,
        )

        res = cast(
            Union[AskElementResponse, None],
            await context.emitter.send_ask_user(
                step_dict,
                spec,
                self.raise_on_timeout,
                # The client loses the element on refresh; it is re-emitted
                # alongside the ask on reconnect. Passed as the live object
                # and serialized at restore time, so updates made while the
                # ask is pending are not rolled back.
                restore_element=self.element,
            ),
        )

        await self.element.remove()

        if res is None:
            self.content = "Timed out"
        elif res.get("submitted"):
            self.content = "Thanks for submitting"
        else:
            self.content = "Cancelled"

        self.wait_for_answer = False

        await self.update()

        return res
