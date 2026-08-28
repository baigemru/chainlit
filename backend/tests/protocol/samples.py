"""One instance of every message in both unions.

The round-trip tests are only as exhaustive as this file, so
``test_roundtrip.py`` asserts that its keys cover ``SERVER_TAGS`` and
``CLIENT_TAGS`` exactly — a new message with no sample fails the suite.
"""

from __future__ import annotations

from chainlit.protocol import client as c, server as s
from chainlit.protocol.codec import ErrorCode
from chainlit.protocol.payloads import (
    Action,
    AskElementReply,
    AskFileSpec,
    CustomElement,
    Feedback,
    FileRef,
    Step,
    StepPatch,
    TextElement,
    Thread,
    Wait,
)

SAMPLE_STEP = Step(
    id="4d8f0e5e-0b3a-4a1e-8a1f-1f0f3a2b9c11",
    output="hello",
    name="Assistant",
    type="assistant_message",
    thread_id="thread-1",
    parent_id=None,
    created_at="2026-08-27T10:00:00Z",
    streaming=True,
    show_input="python",
    metadata={"favorite": True},
    feedback=Feedback(value=1, for_id="4d8f", comment="good"),
    wait=Wait(texts=["thinking", "still thinking"], interval_ms=3000, loop=True),
)

# A patch is deliberately *not* a full step: it sets a couple of fields,
# turns one boolean explicitly off, and stays silent about everything else.
SAMPLE_STEP_PATCH = StepPatch(
    id=SAMPLE_STEP.id,
    output="hello there",
    streaming=False,
    wait=None,
)

SAMPLE_ACTION = Action(
    id="action-1",
    name="confirm",
    payload={"answer": "yes"},
    label="Confirm",
    tooltip="Go ahead",
    icon="check",
    for_id=SAMPLE_STEP.id,
)

SAMPLE_THREAD = Thread(
    id="thread-1",
    created_at="2026-08-27T09:00:00Z",
    name="A chat",
    user_identifier="someone@example.com",
    parent_thread_id="thread-0",
    tags=["profile:default"],
    metadata={"chat_profile": "default"},
    steps=[SAMPLE_STEP],
    elements=[TextElement(id="el-1", name="notes", language="python")],
)


SERVER_SAMPLES: dict[str, s.ServerMsg] = {
    "session.ready": s.SessionReady(
        session_id="session-1",
        thread_id="thread-1",
        chat_profile="default",
        restored=True,
    ),
    "error": s.Error(
        code=ErrorCode.ASK_SLOT_BUSY.value,
        message="An ask is already pending",
        detail={"stepId": SAMPLE_STEP.id},
    ),
    "hb": s.Heartbeat(seq=7),
    "reload": s.Reload(),
    "step.upsert": s.StepUpsert(step=SAMPLE_STEP),
    "step.update": s.StepUpdate(step=SAMPLE_STEP_PATCH),
    "step.delete": s.StepDelete(step_id=SAMPLE_STEP.id),
    "step.stream.start": s.StepStreamStart(step=SAMPLE_STEP),
    "step.stream.token": s.StepStreamToken(
        id=SAMPLE_STEP.id, token="tok", is_sequence=True
    ),
    "element.upsert": s.ElementUpsert(
        element=CustomElement(id="el-2", name="widget", props={"count": 3})
    ),
    "element.remove": s.ElementRemove(id="el-2"),
    "action.add": s.ActionAdd(action=SAMPLE_ACTION),
    "action.remove": s.ActionRemove(id=SAMPLE_ACTION.id),
    "ask.start": s.AskStart(
        spec=AskFileSpec(
            step_id=SAMPLE_STEP.id,
            timeout=120,
            accept={"image/*": [".png", ".jpg"]},
            max_files=3,
            max_size_mb=10,
        ),
        step=SAMPLE_STEP,
    ),
    "ask.end": s.AskEnd(step_id=SAMPLE_STEP.id, reason="timeout"),
    "task.indicator": s.TaskIndicator(running=True),
    "thread.resume": s.ThreadResume(thread=SAMPLE_THREAD),
    "thread.first_interaction": s.ThreadFirstInteraction(
        interaction="resume", thread_id="thread-1"
    ),
    "thread.parent": s.ThreadParent(parent_thread_id="thread-0"),
    "thread.open": s.ThreadOpen(thread_id="thread-0", keep_transcript=False),
    "session.handoff": s.SessionHandoff(
        chat_profile="fast",
        next_session_id="session-2",
        keep_transcript=True,
        has_transit_message=True,
    ),
    "sidebar.set": s.SidebarSet(
        title="Sources",
        elements=[TextElement(id="el-3", name="src", language="markdown")],
        key="sources-v1",
    ),
    "toast": s.Toast(message="Saved", type="success"),
}


CLIENT_SAMPLES: dict[str, c.ClientMsg] = {
    "hello": c.Hello(
        session_id="session-1",
        client_type="copilot",
        thread_id="thread-1",
        chat_profile="default",
        user_env={"OPENAI_API_KEY": "x"},
        page_load=True,
    ),
    "hb.ack": c.HeartbeatAck(seq=7),
    "session.clear": c.SessionClear(),
    "stop": c.Stop(),
    "message.send": c.MessageSend(
        message=SAMPLE_STEP, file_references=[FileRef(id="file-1")]
    ),
    "ask.reply": c.AskReply(
        step_id=SAMPLE_STEP.id,
        value=AskElementReply(submitted=True, props={"choice": "b"}),
    ),
}
