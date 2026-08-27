"""Serialization baseline: today's payload shapes, pydantic vs msgspec.

Measures the encode path Chainlit actually runs -- a StepDict on every token
of a streamed message, an ElementDict per element, a ThreadDict per resume.
"""

import json
import time
from typing import Any

import msgspec
from pydantic import BaseModel


def bench(label: str, fn, n: int) -> tuple[str, float, float]:
    fn()  # warm
    runs = []
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        runs.append(time.perf_counter() - t0)
    best = min(runs)
    return label, n / best, (best / n) * 1e6  # ops/sec, µs/op


STEP: dict[str, Any] = {
    "id": "0195a7d1-3f2e-7a11-9b3c-2f4c1a9e8d77",
    "name": "assistant",
    "type": "assistant_message",
    "threadId": "0195a7d1-3f2e-7a11-9b3c-2f4c1a9e8d78",
    "parentId": None,
    "streaming": True,
    "waitForAnswer": False,
    "isError": False,
    "input": "",
    "output": "Ответ ассистента " * 12,
    "createdAt": "2026-08-27T10:11:12.131415Z",
    "start": "2026-08-27T10:11:12.131415Z",
    "end": None,
    "language": None,
    "showInput": "false",
    "defaultOpen": False,
    "metadata": {"favorite": False, "source": "bench", "tokens": 128},
    "tags": ["bench"],
    "command": None,
}


# ---- pydantic (what the codebase uses today for request/response models) ----
class PydStep(BaseModel):
    id: str
    name: str
    type: str
    threadId: str | None = None
    parentId: str | None = None
    streaming: bool = False
    waitForAnswer: bool = False
    isError: bool = False
    input: str = ""
    output: str = ""
    createdAt: str | None = None
    start: str | None = None
    end: str | None = None
    language: str | None = None
    showInput: str | None = None
    defaultOpen: bool = False
    metadata: dict[str, Any] = {}
    tags: list[str] | None = None
    command: str | None = None


# ---- msgspec (the rebuild) ----
class MsgStep(msgspec.Struct, rename="camel", omit_defaults=True):
    id: str
    name: str
    type: str
    thread_id: str | None = None
    parent_id: str | None = None
    streaming: bool = False
    wait_for_answer: bool = False
    is_error: bool = False
    input: str = ""
    output: str = ""
    created_at: str | None = None
    start: str | None = None
    end: str | None = None
    language: str | None = None
    show_input: str | None = None
    default_open: bool = False
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)
    tags: list[str] | None = None
    command: str | None = None


pyd_obj = PydStep(**STEP)  # type: ignore[arg-type]
msg_obj = msgspec.convert(STEP, MsgStep, strict=False)
raw = json.dumps(STEP).encode()

msg_enc = msgspec.json.Encoder()
msg_dec = msgspec.json.Decoder(MsgStep)

N = 20_000
rows = [
    bench("stdlib json.dumps(dict)", lambda: json.dumps(STEP).encode(), N),
    bench("pydantic model_dump_json", lambda: pyd_obj.model_dump_json().encode(), N),
    bench("msgspec encode(Struct)", lambda: msg_enc.encode(msg_obj), N),
    bench("stdlib json.loads", lambda: json.loads(raw), N),
    bench("pydantic model_validate_json", lambda: PydStep.model_validate_json(raw), N),
    bench("msgspec decode->Struct", lambda: msg_dec.decode(raw), N),
    bench("pydantic construct", lambda: PydStep(**STEP), N),  # type: ignore[arg-type]
    bench(
        # Same 20 fields as the pydantic row above. Constructing a 3-field
        # struct against a 20-field model measures nothing.
        "msgspec Struct construct",
        lambda: msgspec.convert(STEP, MsgStep, strict=False),
        N,
    ),
]

print(f"{'operation':34} {'ops/sec':>12} {'µs/op':>9}")
print("-" * 58)
for label, ops, us in rows:
    print(f"{label:34} {ops:12,.0f} {us:9.2f}")

print("\nratios (msgspec vs pydantic):")
enc = rows[1][1], rows[2][1]
dec = rows[4][1], rows[5][1]
con = rows[6][1], rows[7][1]
print(f"  encode : {enc[1] / enc[0]:5.1f}x faster")
print(f"  decode : {dec[1] / dec[0]:5.1f}x faster")
print(f"  construct: {con[1] / con[0]:5.1f}x faster")

print(f"\npayload bytes: json={len(raw)}  msgspec={len(msg_enc.encode(msg_obj))}")
