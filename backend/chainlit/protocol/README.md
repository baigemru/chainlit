# `chainlit.protocol`

The typed wire protocol for the Litestar rebuild: **52 socket.io event names
with opaque dict payloads → two `msgspec` tagged unions**, 23 server tags and
6 client tags, both discriminated on `t`.

The counts used to be 36 and 17 — one tag per old event. The consumer audit
then showed that a third of them served features that are switched off or
unreachable: audio, chat settings, commands, modes, favorites, message
editing, the copilot RPC into the host page, a token counter whose
client atom was written and never read, and the in-place profile hot swap. Those names are gone, and the
reason each one went is recorded in `INTENTIONALLY_DROPPED` in
`tests/protocol/test_coverage.py`, which still refuses to let an old event
disappear without one.

Pure data plus a codec. Nothing in this package imports another `chainlit`
module, so it can be tested, reviewed and versioned on its own — and the
client rewrite has exactly one directory to read.

```
payloads.py   Step, StepPatch, Wait, Element (union), Action,
              AskSpec (union), AskReplyValue (union), Thread, Command,
              Mode, InputWidgetSpec, Feedback
server.py     ServerMsg  — 23 branches, tag_field="t"
client.py     ClientMsg  — 6 branches, tag_field="t"
codec.py      encode/decode, CloseCode, ErrorCode
```

Frames: **JSON, always.** There is no binary branch. The only messages that
ever needed one were the two audio chunks; audio is off, and file upload
and download were already HTTP, so nothing left on this wire carries
`bytes`. A second encoder would now be a feature with no user.

---

## Server → client

37 old events → 23 tags (four collapsed pairs, three new messages,
fourteen names retired with the features behind them).

| Old event              | New tag                             | Note                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `new_message`          | `step.upsert`                       |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `update_message`       | `step.update`                       | **Not** merged into `step.upsert`: an upsert creates the step when the id is unknown, an update addresses one that must already exist. Merging them would turn a late update into a new bubble at the bottom of the feed. Payload is a **`StepPatch`**, not a `Step`: every field but `id` is absent unless stated, so `"streaming": false` means "stop streaming" while an absent `streaming` means "no opinion" — a `Step`'s value defaults could not tell the two apart under `omit_defaults`. `"wait": null` ends wait mode; an absent `wait` leaves it. |
| `delete_message`       | `step.delete`                       | Carries `stepId` only; the old event shipped the whole step dict to delete it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `stream_start`         | `step.stream.start`                 |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `stream_token`         | `step.stream.token`                 |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `element`              | `element.upsert`                    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `remove_element`       | `element.remove`                    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `action`               | `action.add`                        |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `remove_action`        | `action.remove`                     | Carries `id` only; the old event shipped the whole action dict.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `ask`                  | `ask.start`                         |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `ask_timeout`          | `ask.end` `{reason: "timeout"}`     | **Collapsed pair.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `clear_ask`            | `ask.end` `{reason}`                | `reason ∈ answered \| timeout \| cancelled \| superseded \| stale`. Now **addressed** by `stepId` — see _Correlation_ below.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `task_start`           | `task.indicator` `{running: true}`  | **Collapsed pair.** One level-triggered boolean that was split over two names, forcing every resync in `emitter.py` to pick which of the two to emit.                                                                                                                                                                                                                                                                                                                                                                                                        |
| `task_end`             | `task.indicator` `{running: false}` |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `resume_thread`        | `thread.resume`                     |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `resume_thread_error`  | **retired**                         | folded into `error` with a code                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `first_interaction`    | `thread.first_interaction`          |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `parent_thread`        | `thread.parent`                     |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `open_thread`          | `thread.open`                       |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `chat_profile_changed` | **retired**                         | in-place hot swap: `hot_swap_chat_profile` is off and no `on_profile_start` hook exists; the profile is chosen in `hello` and changed server-side by `session.handoff`                                                                                                                                                                                                                                                                                                                                                                                       |
| `set_chat_profile`     | `session.handoff`                   | **Renamed, not just retagged.** It tears the session down, mints a successor session id and parks a transit record — while sitting one letter away from the in-place `switch_chat_profile`.                                                                                                                                                                                                                                                                                                                                                                  |
| `chat_settings`        | **retired**                         | chat settings are unused                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `set_commands`         | **retired**                         | commands are unused                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `set_modes`            | **retired**                         | modes are unused                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `set_favorites`        | **retired**                         | `favorites = false`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `set_sidebar_title`    | `sidebar.set` `{title}`             | **Collapsed pair.** The client reconciled both into one `sideView` atom, each handler reading the other's half out of the previous state.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `set_sidebar_elements` | `sidebar.set` `{elements, key}`     | An **absent** field means "leave it alone"; `"title": null` / `"key": null` clears it. `elements` has no null form — an empty list closes the sidebar.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `audio_connection`     | **retired**                         | audio is disabled                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `audio_chunk`          | **retired**                         | audio is disabled; the wire has no binary frames                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `audio_interrupt`      | **retired**                         | audio is disabled                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `call_fn`              | **retired**                         | no copilot embedding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `clear_call_fn`        | **retired**                         | no copilot embedding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `call_fn_timeout`      | **retired**                         | no copilot embedding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `toast`                | `toast`                             |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `token_usage`          | **retired**                         | the client atom it fed is never read                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `window_message`       | **retired**                         | no host-page integration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `reload`               | `reload`                            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| —                      | `session.ready`                     | **New.** The handshake is complete. socket.io had no such message: the client emitted `connection_successful` and then guessed, which is why `switch_chat_profile` and the orphaned-`ask_reply` conversion both park on an internal `connection_inited` gate today.                                                                                                                                                                                                                                                                                          |
| —                      | `error`                             | **New.** A refusal the client can act on, carrying an `ErrorCode`. Today a failure is signalled by silence, by a socket.io refusal string, or by an `ErrorMessage` step in the transcript.                                                                                                                                                                                                                                                                                                                                                                   |
| —                      | `hb`                                | **New.** Liveness probe; the client answers `hb.ack`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

## Client → server

17 old events → 6 tags (one 2→1 collapse, one drop, one new message,
ten names retired with the features behind them).

| Old event               | New tag         | Note                                                                                                                                                                                                                                                               |
| ----------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `connect` (auth dict)   | `hello`         | **2 → 1.**                                                                                                                                                                                                                                                         |
| `connection_successful` | `hello`         | The split is the source of the ordering hazard worked around all over `socket.py`: the client flushes its send buffer _before_ emitting `connection_successful`, so buffered events reach a half-initialised session. One first frame, one `session.ready` answer. |
| `disconnect`            | _dropped_       | Transport-level: the websocket close frame replaces it. socket.io synthesises it as an event; a raw websocket does not need a name for it in the message vocabulary.                                                                                               |
| `clear_session`         | `session.clear` |                                                                                                                                                                                                                                                                    |
| `switch_chat_profile`   | **retired**     | in-place hot swap is off; the selector reconnects with the new profile in `hello`                                                                                                                                                                                  |
| `stop`                  | `stop`          |                                                                                                                                                                                                                                                                    |
| `ask_reply`             | `ask.reply`     | Still a plain message, never a request/response ack — it has to survive being buffered across a reconnect, which a socket.io ack (bound to the socket id) does not.                                                                                                |
| `client_message`        | `message.send`  |                                                                                                                                                                                                                                                                    |
| `edit_message`          | **retired**     | `edit_message = false`                                                                                                                                                                                                                                             |
| `message_favorite`      | **retired**     | `favorites = false`                                                                                                                                                                                                                                                |
| `fetch_favorites`       | **retired**     | `favorites = false`                                                                                                                                                                                                                                                |
| `window_message`        | **retired**     | no host-page integration                                                                                                                                                                                                                                           |
| `audio_start`           | **retired**     | audio is disabled                                                                                                                                                                                                                                                  |
| `audio_chunk`           | **retired**     | audio is disabled; the wire has no binary frames                                                                                                                                                                                                                   |
| `audio_end`             | **retired**     | audio is disabled                                                                                                                                                                                                                                                  |
| `chat_settings_change`  | **retired**     | chat settings are unused                                                                                                                                                                                                                                           |
| `chat_settings_edit`    | **retired**     | chat settings are unused                                                                                                                                                                                                                                           |
| —                       | `hb.ack`        | **New.** Answer to `hb`.                                                                                                                                                                                                                                           |

---

## Correlation

One message family used to be _unaddressed_, and grew elaborate server-side
choreography to compensate. (There were two: the other was the `call_fn`
reply, correlated through a socket.io ack bound to the socket id and
therefore unable to survive a reconnect. It is retired along with the
copilot embedding, so the problem is gone rather than solved.)

- **`ask.end` carries `stepId`.** The entire "never emit `clear_ask` over a
  live successor ask" dance in `socket.py` and `emitter.py` exists only
  because `clear_ask` addressed nothing. An addressed end lets the client
  drop a stale one itself.

## Field-level renames

All structs are `rename="camel"`, so the snake_case corners of today's wire
move. The client rewrite needs this list as much as the tag map.

| Old field          | New field        | Where                      |
| ------------------ | ---------------- | -------------------------- |
| `spec.step_id`     | `spec.stepId`    | `ask.start`                |
| `spec.element_id`  | `spec.elementId` | `ask.start` (element spec) |
| `spec.max_files`   | `spec.maxFiles`  | `ask.start` (file spec)    |
| `spec.max_size_mb` | `spec.maxSizeMb` | `ask.start` (file spec)    |
| `thread_id`        | `threadId`       | `thread.first_interaction` |

Everything else was already camelCase (`forId`, `chainlitKey`, `parentId`,
`isSequence`, `keepTranscript`, `nextSessionId`, `hasTransitMessage`,
`parentThreadId`, `autoPlay`, `playerConfig`, `intervalMs`).

## Shape changes worth flagging

- **Bare payloads are wrapped.** `token_usage` sent a bare integer,
  `audio_connection` a bare string, `set_sidebar_title` a bare string,
  `chat_settings` / `set_commands` / `set_modes` / `set_favorites` bare
  arrays, `resume_thread_error` a bare string. Every message is now a struct,
  so any of them can gain a field without a version break.
- **The `Element` union puts per-type fields on their own branch.** Today's
  `ElementDict` is flat and `total=False`, so a `pdf` may carry `autoPlay`
  and a `text` may carry `props`. Now `props` exists only on `custom`, `page`
  only on `pdf`, `autoPlay` only on `audio`, `playerConfig` only on `video`,
  `language` only on `text`, and `size` only on `image` and `video`.
- **The element ask reply nests its props.** Today it is
  `{**props, "submitted": True}` — arbitrary app keys spread over the top
  level, where a prop named `type` or `id` can shadow a protocol field (the
  `_is_convertible_text_reply` gate in `socket.py` exists partly to defend
  against exactly that). It is now
  `{kind: "element", submitted: bool, props: {...}}`.
- **`Feedback` requires `value` and `forId`**, as the legacy
  `types.Feedback` dataclass did. The first cut of this struct defaulted
  them, and `0` is a thumbs-_down_ — so `Feedback()` was a silent negative
  rating of nothing at all. Neither field has a default.
- **`step.update` carries a `StepPatch`.** See the table above: absent means
  "no opinion", `false` / `""` / `null` are stated values. `step.upsert`,
  `step.stream.start` and `ask.start` keep the full `Step` — they state the
  whole object, so a value default is the value.
- **`AskReplyValue` is a tagged union** on `kind`, because msgspec permits at
  most one struct type in an untagged union and a text reply, an action reply
  and an element reply are all structs. The client stamps the tag.

## Forward compatibility

Unknown fields are **ignored**, not rejected. `@chainlit/react-client` ships
on its own release cycle, so a newer peer must be able to add a field without
breaking an older one. What a decoder _does_ reject is an unknown `t` tag, a
field of the wrong type, and a missing required field — the failures that mean
"this message is not what it claims to be" rather than "this message is newer
than I am". `Hello.protocolVersion` is the escape hatch for a change that
cannot be made additively.

## Close codes

| Code | Meaning                                                       |
| ---- | ------------------------------------------------------------- |
| 4400 | bad handshake — the first frame was not a well-formed `hello` |
| 4401 | unauthenticated                                               |
| 4403 | session forbidden — the session id belongs to another user    |
| 4404 | thread forbidden — the thread is not readable by this user    |
| 4408 | heartbeat timeout — no `hb.ack` within the deadline           |
| 4409 | superseded — another connection took this session over        |
| 4413 | frame too large                                               |
| 4500 | internal                                                      |

`ErrorCode` (on the `error` message) is separate: an error leaves the socket
open. A failure that must also close it sends both.
