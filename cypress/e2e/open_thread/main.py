import os
from typing import Dict, List, Optional

import chainlit as cl
import chainlit.data as cl_data
from chainlit.data.utils import queue_until_user_message
from chainlit.element import Element, ElementDict
from chainlit.step import StepDict
from chainlit.types import (
    Feedback,
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.utils import utc_now

os.environ["CHAINLIT_AUTH_SECRET"] = "SUPER_SECRET"  # nosec B105

now = utc_now()

thread_history = []  # type: List[ThreadDict]


class TestDataLayer(cl_data.BaseDataLayer):
    async def get_user(self, identifier: str):
        return cl.PersistedUser(id="user1_id", createdAt=now, identifier=identifier)

    async def create_user(self, user: cl.User):
        return cl.PersistedUser(
            id="user1_id", createdAt=now, identifier=user.identifier
        )

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        parent_thread_id: Optional[str] = None,
    ):
        thread = next((t for t in thread_history if t["id"] == thread_id), None)
        if thread:
            if name:
                thread["name"] = name
            if metadata:
                thread["metadata"] = metadata
            if tags:
                thread["tags"] = tags
            if parent_thread_id:
                thread["parentThreadId"] = parent_thread_id
        else:
            thread_history.append(
                {
                    "id": thread_id,
                    "name": name,
                    "metadata": metadata,
                    "tags": tags,
                    "createdAt": utc_now(),
                    "userId": user_id,
                    "userIdentifier": "user1",
                    "parentThreadId": parent_thread_id,
                    "steps": [],
                }
            )

    @cl_data.queue_until_user_message()
    async def create_step(self, step_dict: StepDict):
        thread = next(
            (t for t in thread_history if t["id"] == step_dict.get("threadId")), None
        )
        if thread:
            thread["steps"].append(step_dict)

    async def get_thread_author(self, thread_id: str):
        thread = await self.get_thread(thread_id)
        return thread["userIdentifier"] if thread else None

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        return PaginatedResponse(
            data=thread_history,
            pageInfo=PageInfo(hasNextPage=False, startCursor=None, endCursor=None),
        )

    async def get_thread(self, thread_id: str):
        thread = next((t for t in thread_history if t["id"] == thread_id), None)
        if not thread:
            return None
        thread["steps"] = sorted(thread["steps"], key=lambda x: x["createdAt"])
        return thread

    async def delete_thread(self, thread_id: str):
        pass

    async def delete_feedback(self, feedback_id: str) -> bool:
        return True

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return ""

    @queue_until_user_message()
    async def create_element(self, element: "Element"):
        pass

    async def get_element(
        self, thread_id: str, element_id: str
    ) -> Optional["ElementDict"]:
        return None

    @queue_until_user_message()
    async def delete_element(self, element_id: str, thread_id: Optional[str] = None):
        pass

    @queue_until_user_message()
    async def update_step(self, step_dict: "StepDict"):
        pass

    @queue_until_user_message()
    async def delete_step(self, step_id: str):
        pass

    async def get_favorite_steps(self, user_id: str) -> List["StepDict"]:
        return []

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        pass


@cl.data_layer
def data_layer():
    return TestDataLayer()


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    if (username, password) == ("user1", "user1"):
        return cl.User(identifier="user1")
    return None


@cl.set_chat_profiles
async def chat_profiles(current_user):
    return [
        cl.ChatProfile(
            name="Assistant",
            markdown_description="General assistant",
            default=True,
        ),
        cl.ChatProfile(name="Search", markdown_description="Product search"),
    ]


@cl.on_chat_start
async def on_chat_start():
    if cl.user_session.get("chat_profile") == "Search":
        await cl.Message(content="search ready").send()
        transit = cl.user_session.get("transit_message")
        if transit is not None:
            cl.user_session.set("transit_message", None)
            await cl.Message(
                content=str(transit), type="user_message", author="user1"
            ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    # Marker: proves the parent thread was resumed by the return.
    await cl.Message(content="RESUMED").send()


@cl.on_message
async def on_message(msg: cl.Message):
    if msg.content.startswith("go soft"):
        await cl.context.emitter.set_chat_profile(
            "Search", keep_transcript=True, transit_message="child chat query"
        )
    elif msg.content.startswith("back"):
        # The agent-driven return: same client handler as the button.
        parent = getattr(cl.context.session, "parent_thread_id", None)
        if parent:
            await cl.context.emitter.open_thread(parent)
        else:
            await cl.Message(content="no parent").send()
    else:
        await cl.Message(
            content=f"profile: {cl.user_session.get('chat_profile')}"
        ).send()
