# Backend architecture (`backend/chainlit/`)

This fork runs on **Litestar 2.24**, msgspec, advanced-alchemy and uvicorn, Python 3.14 only,
PostgreSQL only (asyncpg). FastAPI, Starlette, pydantic and socket.io are gone and are not
coming back — `backend/tests/test_import_hygiene.py` fails the build on a direct import of any
of them. Distribution name `chainlit-litestar`; import name `chainlit`.

Everything below was read out of the source. Where a docstring states a rationale, it is
usually a bug that was fixed — treat it as normative.

---

## 1. Package map

Public API means: exported from `backend/chainlit/__init__.py` `__all__` and meant to be
called as `cl.*` by an application author. Everything else is internal.

| Path                                                                                              | Purpose                                                                                                                                                                                                                                       | Surface                |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `__init__.py`                                                                                     | Re-exports the `cl.*` API; loads `.env` before any other import.                                                                                                                                                                              | public                 |
| `plugin.py`                                                                                       | `ChainlitPlugin(InitPlugin)` — the entire integration surface with a host `Litestar`.                                                                                                                                                         | public (embedding)     |
| `runner.py`                                                                                       | `ApplicationRunner`: runs `config.code` on behalf of sessions; the only place that sets the context var.                                                                                                                                      | internal               |
| `ws/session.py`                                                                                   | `Session` — a conversation, independent of the socket carrying it. Imports nothing from the `cl.*` layer.                                                                                                                                     | internal               |
| `ws/connection.py`                                                                                | The `@websocket("/ws")` route, `Connection`, the reader and heartbeat loops.                                                                                                                                                                  | internal               |
| `ws/outbound.py`                                                                                  | `Outbound` — one bounded queue, one writer task, one owner of the close.                                                                                                                                                                      | internal               |
| `ws/registry.py`                                                                                  | `SessionRegistry` — one session per thread, keyed by thread and indexed by handle. Imports nothing from `chainlit` or the transport.                                                                                                          | internal               |
| `ws/handshake.py`                                                                                 | `arrive` / `ready_frame` / `restore` — what a `hello` is allowed to mean.                                                                                                                                                                     | internal               |
| `ws/sidebar.py`                                                                                   | `SidebarState` — the element panel as session state, its invariants, what one `sidebar.user` frame does to it, and its projection to and from thread metadata (`__sidebar`).                                                                  | internal               |
| `protocol/{server,client,payloads,codec}.py`                                                      | msgspec tagged unions on `t`, plus `CloseCode`/`ErrorCode`. Imports no other `chainlit` module.                                                                                                                                               | internal (stable wire) |
| `protocol/README.md`                                                                              | Old-event → tag map, renames, shape changes, close codes.                                                                                                                                                                                     | docs                   |
| `controllers/auth.py`                                                                             | `/auth/*`, `/login`, `/logout`, `/user`.                                                                                                                                                                                                      | internal               |
| `controllers/project.py`                                                                          | Threads, elements, feedback, actions, `/project/settings`, `/health`.                                                                                                                                                                         | internal               |
| `controllers/files.py`                                                                            | Upload/download, `/favicon`, `/logo`, `/avatars/*`.                                                                                                                                                                                           | internal               |
| `controllers/index.py`                                                                            | `render_index` — fills the built SPA shell with title, favicon, OG tags, theme.                                                                                                                                                               | internal               |
| `controllers/sessions.py`                                                                         | `LiveSession` / `SessionRegistry` protocols the routes are allowed to see.                                                                                                                                                                    | internal               |
| `controllers/account.py`                                                                          | `AccountController` (`/project/account`, its `/actions/{name}`) and the `require_identity` guard.                                                                                                                                             | internal               |
| `controllers/caller.py`                                                                           | `caller`, `caller_identifier`, `assert_session_owner` — reading the scope safely.                                                                                                                                                             | internal               |
| `persistence/`                                                                                    | `records` → `models` → `statements` → `repositories`/`services` → `config` → `writer`.                                                                                                                                                        | internal               |
| `persist.py`                                                                                      | The `cl.*` → rows seam: `save_step`, `save_element`, `delete_*`, `open_thread`, `thread_state`; the panel's `drop_elements`, `patch_sidebar`; `split_engine_metadata`.                                                                        | internal               |
| `security.py`                                                                                     | `ChainlitAuth(JWTCookieAuth)`, `Identity`, `identity_from_token`, `chainlit_auth()`.                                                                                                                                                          | internal               |
| `oauth_providers.py`                                                                              | The configured OAuth providers and their token exchanges.                                                                                                                                                                                     | internal               |
| `transit_store.py`                                                                                | `TransitStore` — the TTL'd one-shot profile-switch handover on a `litestar.stores` store.                                                                                                                                                     | internal               |
| `config.py`                                                                                       | `.chainlit/config.toml` decoded with msgspec, plus `config.code` (the registered callbacks).                                                                                                                                                  | internal               |
| `account.py`                                                                                      | `@cl.account`'s rules, the JSON Schema, the lenient decode of a stored object, the action outcomes and `element_type_at`.                                                                                                                     | public (`cl.account`)  |
| `account_badge.py`                                                                                | The one recompute-and-push of the `account.badge` frame, called from the handshake, the route and the emitter.                                                                                                                                | internal               |
| `callbacks.py`                                                                                    | The `@cl.on_*` decorators; each stores a wrapped function on `config.code`.                                                                                                                                                                   | public                 |
| `context.py`                                                                                      | `ChainlitContext`, `context_var`, `init_context`, and the `cl.context` proxy.                                                                                                                                                                 | public (`cl.context`)  |
| `emitter.py`                                                                                      | `Emitter` — one method per thing the app can put on screen; produces frames, never rows.                                                                                                                                                      | internal               |
| `message.py`                                                                                      | `Message`, `ErrorMessage`, `AskUserMessage`, `AskActionMessage`, `AskFileMessage`, `AskElementMessage`.                                                                                                                                       | public                 |
| `step.py`                                                                                         | `Step` and the `@cl.step` decorator.                                                                                                                                                                                                          | public                 |
| `element.py`                                                                                      | `Image`, `Pdf`, `Text`, `File`, `Video`, `Audio`, `Plotly`, `Pyplot`, `Dataframe`, `CustomElement`, `TaskList`.                                                                                                                               | public                 |
| `action.py`                                                                                       | `Action` — a button attached to a message.                                                                                                                                                                                                    | public                 |
| `user_session.py`                                                                                 | `cl.user_session` — a thin view over `Session.state`.                                                                                                                                                                                         | public                 |
| `chat_context.py`                                                                                 | `cl.chat_context` — the conversation's messages, kept on the session's state.                                                                                                                                                                 | public                 |
| `sidebar.py`, `mode.py`, `types.py`, `user.py`                                                    | `Sidebar` (the element panel's application half; the model itself is `ws/sidebar.py`), `Mode`/`ModeOption`, `ThreadDict`/`ChatProfile`/`Starter`, `User`/`PersistedUser`.                                                                     | public                 |
| `cli/__init__.py`                                                                                 | The `chainlit` command: `run`, `hello`, `init`, `create-secret`, `lint-translations`.                                                                                                                                                         | public (CLI)           |
| `utils.py`, `_utils.py`, `secret.py`, `markdown.py`, `logger.py`, `translations.py`, `version.py` | Helpers, secret generation, `chainlit.md` bootstrap, the `chainlit` logger, translation linting, `__version__`.                                                                                                                               | internal               |
| `frontend/dist`, `translations/*.json`, `sample/`                                                 | Built JS artefacts (not in git), shipped UI translations (the base `load_translation` lays the app's `.chainlit/translations/` copy over, so a key a release adds arrives without the app touching its copy), the `chainlit hello` demo apps. | assets                 |

---

## 2. How an application is assembled

There is **one** Litestar application. `ChainlitPlugin.on_app_init` contributes everything
Chainlit needs into the host's own `AppConfig` (`plugin.py`):

- **Routes** — `AuthController`, `ProjectController`, `FilesController` and the `/ws` handler,
  gathered under one `Router(path="/")` that owns Chainlit's `request_max_body_size` and a
  `NotFoundException` handler that refuses to fall back to the SPA. Plus static routers for
  `/public` (the app's `public/`) and `/assets` (the bundle), both `exclude_from_auth`.
- **SPA fallback** — `make_spa_fallback`, registered with `setdefault` on both
  `NotFoundException` **and** `MethodNotAllowedException` (`/login` is a POST route _and_ a
  page). It answers only when `Accept` contains `text/html`; an API miss stays a JSON 404.
- **Auth middleware** — when auth is on, `self._auth.on_app_init(app_config)` inserts
  `JWTCookieAuth`'s middleware at position 0. Its scopes are `{http, websocket}`, which is how
  the upgrade request is authenticated.
- **Dependencies** — `sessions`, `transit`, `persistence_enabled`, `security`, `user_service`, all with
  `setdefault` so a host keeps its own bindings. With no persistence, `_bind_absent_services`
  binds `users`/`threads`/`steps`/`elements`/`feedbacks` to providers that raise
  `ServiceUnavailableException`, so routes that do not need a database still mount.
- **Persistence plugin** — when `persistence` is passed, the plugin appends
  `persistence.plugin()` (advanced-alchemy's `SQLAlchemyPlugin`) to `app_config.plugins` and
  merges `persistence.dependencies()`. Litestar iterates `config.plugins` lazily, so appending
  during `on_app_init` works — and it makes plugin-ordering mistakes unreachable.
- **Stores** — the `TransitStore` is registered under `chainlit_transit`, never replacing a
  host's registry.
- **Lifespan** — `bootstrap()` (entry-point check, auth-secret check, `chainlit.md`), then
  `on_app_startup`, then the transit sweeper; on exit `on_app_shutdown` and `rmtree(FILES_DIRECTORY)`.

There is deliberately **no** `create_app` factory and no `mount_chainlit`.

**The account page.** An application declares **one** `msgspec.Struct` with `@cl.account`, and
the engine derives everything else from it: `msgspec.json.schema` is the form the client
renders, `msgspec.convert` is the validator a save runs through, `msgspec.to_builtins` is what
goes into `users.account`, and the same type documents the route. Nothing is mirrored by hand,
which is what the retired widget layer (`input_widget.py`, `InputWidgetSpec`, `ChatSettings`)
was — it is deleted, not shimmed. `chainlit/account.py` holds the rules: the argument must be a
Struct and **every** field must have a default, recursively, or the page cannot be drawn for a
user who has never saved and a stale stored value has no baseline to be dropped back to. The
walk is over `msgspec.inspect.type_info`, so `Annotated`, `X | None` and `list[X]` are not
places to hide a required field.

`AccountController` (`controllers/account.py`) serves `GET`/`PUT /project/account` behind
`guards=[require_identity]` — a guard, so an anonymous request never reaches a database session;
it reads the scope the way `controllers/caller.py` does, because with no `CHAINLIT_AUTH_SECRET`
the `user` property _raises_. The GET answers the schema, the values and a `readonly` flag; the
values are `on_account_load`'s return if the app registered one, else the stored object decoded
leniently (a key the Struct no longer declares, or a value that no longer converts, is dropped
with a warning and the rest is kept), else the defaults. The PUT converts the body strictly and
passes msgspec's own message through as the 400 `detail` — "Expected `float` <= 100.0 - at
`$.calculation.margin`" — because the path in it is the only field addressing the client has;
then `on_account_update`, then the store, then the page rebuilt the way the GET builds it.
`405` when there is neither a hook nor a data layer, `404` when no Struct is registered. An
application that keeps the values itself registers **both** hooks: with only `on_account_update`
the next load reads whatever the engine stored — or the defaults, when there is no data layer.
The hooks are stored unwrapped, not through `wrap_user_function`: a hook that raises fails the
request as a 500 rather than being logged away while the engine stores and answers "saved".
There
is deliberately no `[UI]` mirror of "is an account registered": the 404 **is** the
not-configured state. `/account` itself stays a client-side route — no server handler may live
there, or the SPA loses the page.

The schema conventions the client understands are plain JSON Schema plus three `x-` keys
carried through `Meta(extra_json_schema=...)`: `x-enum-labels` maps an enum value to its label,
`x-widget` picks a control (`slider`, `textarea`, `password`, `radio`, `markdown`, `link` —
a `readOnly` string rendered as a button-styled anchor to the value, which is how an
application puts «Платёжный кабинет» on the page pointing at its own `/billing/portal`
redirect — and `cards`, `image`, `title` for a `list[Struct]` drawn as one card per element),
and `x-actions` puts buttons on a card array or on a tab. `[UI.account]` in `config.toml`
decides only whether the user menu shows the row and what it is called; what the page
_contains_ is the Struct.

**Actions.** `POST /project/account/actions/{name}` runs the hook `@cl.account_action(name)`
registered, under the same controller and the same guard. The body is
`{"path": "watch.items.3", "item": {…}}`: `path` is the dotted address of the card the button
sits on, or the field name of a tab (whose `item` is `null`). The element's **type is resolved
from the registered Struct by walking that path** (`account.element_type_at`, over
`msgspec.inspect.type_info`, peeling `Annotated` and `X | None` and stepping into a list's item
type on a numeric segment) — never from an annotation on the hook, which would be a second
declaration of the thing the user's form was drawn from and free to disagree with it. The item
is then `msgspec.convert`ed to that type, so the hook is handed a typed object. An unknown
action is a 404, an unaddressable `path` or an item that does not fit is a 400 carrying
msgspec's own message, and the hook is stored unwrapped like the other two.

The hook returns one of three Structs, and the answer is a **tagged union** discriminated on
`t` — not one Struct with three optional fields, which could mean two things at once:

| returns                                                                 | answer                                                                                                    | what the client does                                                                             |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `cl.AccountToast(message)`                                              | `{"t": "toast", …}`                                                                                       | toasts                                                                                           |
| `cl.AccountRefresh(message=None)`                                       | `{"t": "page", "page": …}`                                                                                | replaces the page without a reload; the page is built by the same `_current` the GET and PUT use |
| `cl.AccountOpenThread(chat_profile, transit_message=None, parent=None)` | `{"t": "open_thread", "thread_id" (null when nothing was parked), "chat_profile", "has_transit_message"}` | takes the same code path `session.handoff` takes                                                 |

Anything else is a `TypeError` and a 500. The handover is **the existing transit, one
implementation**: `transit_store.mint_handover` mints the successor's thread and parks the
record under it, and both `emitter.set_chat_profile` and this route call it — the emitter keeps
only the session state around it (discarding the id a previous switch parked). The value never
travels through the browser: the client is told a thread id and a boolean, the arriving socket
claims the record in `ws/handshake.py`, `TransitStore.claim` refuses a foreign owner, and the
application reads `cl.user_session.get("transit_message")` in `on_chat_start`. The route
receives the store through the `transit` dependency the plugin provides by `setdefault`,
next to `sessions`.

**The badge.** `@cl.on_account_badge(user) -> int` says how many things the page holds that the
user has not seen, and the number is **pushed** on the socket as `account.badge` — there is no
route to poll and nothing on the client zeroes it. `chainlit/account_badge.py` holds the one
recompute-and-push, and three places call it: `runner.on_ready` (every hello, because a browser
that has been shut for a day knows nothing and a reconnect is exactly when its copy is stale),
`AccountController.read` after the page is built (the application marks things seen inside
`on_account_load`, so the count changes as the page is served), and
`cl.context.emitter.refresh_account_badge()` (a run in the chat moved it). Each pushes to
**every** live session of that user — `SessionRegistry.sessions_of`, scanning with the same
`is_owned_by` the takeover uses — because the page and the chat are usually two tabs and only
one of them did the thing. No hook means no frame, ever. The handshake's call swallows a hook
that raises: an exception escaping `on_ready` lands in the connection's task group and closes
the socket, and nobody asked for this number there. The other two let it raise, because they
are answering a request the application made.

`[[UI.user_menu_links]]` is gone with it — the user menu is the name, the account row and
logout, and a link under the icon was the stand-in for the page that now exists.

**Two entry points, one wiring.** `chainlit run app.py` (`cli/__init__.py:build_app`) loads the
user's module, then builds `Litestar(plugins=[ChainlitPlugin(config, persistence=..., configure_logging=True)])`
and serves it with uvicorn. Persistence is opt-in via `DATABASE_URL`. A prefix is passed to
uvicorn as `root_path`, never as `Litestar(path=...)`. Embedding is the same plugin in the
host's own `Litestar(...)`; the host must set `request_max_body_size` itself if it wants
Chainlit's limit on its own routes.

**Configuration.** `.chainlit/config.toml` under `APP_ROOT` (`CHAINLIT_APP_ROOT`, default cwd)
is decoded into `msgspec.Struct` sections — a wrong type or an out-of-range literal is refused
at startup, unknown keys are ignored so an older file still loads. `ChainlitConfig` itself is a
plain class; `config.code` (a `CodeSettings` dataclass) holds what the `@cl.*` decorators
registered.

---

## 3. The websocket

One raw `@websocket("/ws")` handler (`ws/connection.py`). This is the **only** duplex option in
Litestar 2.24: `websocket_listener` is turn-taking, `websocket_stream` is send-only, Channels'
`Subscriber` drops frames on a full queue. Do not re-litigate this.

**Objects.** `Session` is the conversation and outlives its sockets. `Connection` is one
accepted socket and holds what a second socket must not share: `generation` (never reused),
`seq`/`last_ack` (this socket's heartbeat state), and `current` — the only question any loop
asks. `session.current` is the single owner; a connection that has lost it does nothing further
to the session.

**Identity.** The client names a **thread** and nothing else — `hello` carries no session id.
The server mints the session handle and announces it in `session.ready`; the client keeps it in
memory (never in storage) and uses it to address HTTP calls that act on live objects: uploads,
action buttons, custom-element writes. There is no refusal in the handshake: every `hello`
opens.

**Handshake.** Accept → `_first_hello` (10s deadline, must be a well-formed `hello`, else close
4400/4413) → `arrive(...)` → `on_arrival` → `Connection` → `_take_over` → `_serve`.

**One arrival at a time per conversation.** Everything from `arrive` to `_take_over` runs under
a per-thread `asyncio.Lock` (`arrivals`, a `WeakValueDictionary` in the handler closure; a hello
naming no thread takes none). `arrive` registers and returns, then `on_arrival` does database
work — so without it two hellos for one thread resolved **backwards**: the second claimed the
session the first had just registered, adopted it and started replaying, and then the first woke
from its `_resume` and adopted it back. The tab the user opened last was the one closed 4409,
and the frames it had queued drained onto the tab it replaced. The key is the thread the client
_asked_ for, not the one its session ends up in — `_disown_thread` may move it, which leaves the
requested thread free for whoever was waiting, exactly as it should. Not in the registry, which
is lock-free and synchronous by design: this is the opposite concern, a stretch of the handshake
that must await.

**`_take_over` order** is load-bearing and must not be reordered:

1. `session.adopt(connection)` — become current first, so the previous handler's loops are
   already stale.
2. `await session.outbound.detach()` — stop the previous writer _before_ a frame is queued,
   or `session.ready` drains onto the dying socket.
3. `outbound.send(ready, first=True)` then `outbound.attach(socket)` — the ready frame goes to
   the _front_ of the backlog; whatever the previous socket never took is a continuation the
   client is entitled to, but after the frame it resets on.
4. The goodbye to the previous connection is **returned**, not awaited here — it runs as a task
   inside `_serve`'s group so a frozen peer's close timeout cannot hold the new client's replay.

**`_serve`** opens one `anyio.create_task_group()` with `_read_loop`, `_heartbeat`, the optional
goodbye, then `await restore(...)` and `await on_ready()` — restore runs _concurrently with the
reader_ on purpose, because an answer typed before the reload arrives during it. Exceptions are
caught with `except*` (anyio wraps even a single child exception in an `ExceptionGroup`).

**`Outbound`** belongs to the session; the writer task belongs to the socket. `send` never
blocks, awaits or raises. Frames are **peeked, not popped** — a frame lost mid-`send_text` is
resent by the next writer (upserts are idempotent; a hole is not recoverable). Backlog is
bounded at `DEFAULT_MAX_BACKLOG = 1024` frames; overflow closes the connection with 4429 rather
than dropping a frame. Three terminations: `drop` ends the _connection_ and keeps the queue,
`abort` is terminal and discards, `close` flushes then closes. A stop the writer cannot answer
within `FORCE_CLOSE_GRACE = 1.0s` cancels it and closes from the aborting task.

**`SessionRegistry`** is keyed by `thread_id` — one session per conversation — with a secondary
index by session handle for the HTTP routes (`find`, `get`, `mark_*`, `discard`). `register`
raises `ThreadHeld` if the thread is taken, and nothing may `await` between `claim` and the
`register` that acts on it. `claim(thread_id, user)` returns two outcomes: `KEPT` (the thread is
held and it is this user's — the session takes this socket and the previous one is closed 4409)
or `CREATED` (nobody is in it, **or** somebody else is; a foreign thread is answered with a
fresh one of the caller's own and its tenant is not touched). Why the socket opened is not
consulted: a reload, a duplicated tab and a transport blip all say the same sentence.
`has_live_task(thread)` and `protected_step_ids(thread)` are single lookups.

**`ApplicationRunner`** is the application half: `make_session` (mints a thread id and, with
persistence, a `SessionWriter(hold_until_interaction=True)`), `on_arrival` — the **one** place
that decides start / resume / nothing — `on_ready` (which only carries out what `on_arrival`
decided), `on_disconnect`, `_reap`, `relinquish`/`release`/`release_soon`, `teardown`,
`on_message`, `on_stop`, `call_action`.
`requested_thread_id` is what the client _asked_ to resume; `thread_id` may be the id the
session was minted with, and only an asked-for thread can be reported missing.

### Sequences

**Fresh visit.** `hello{pageLoad:true}` naming no thread → `claim` = `CREATED` →
`make_session` mints a handle and a thread → registered → `on_arrival`: `_claim_transit` finds
nothing, `_resume` returns false (nothing was requested), `chat_started = True`,
`start_chat = True` → `session.ready{restored:false}` naming both ids → `restore` sends nothing
but `task.indicator{running:false}` → `on_ready` launches `on_chat_start` as the session's
`current_task`.

**Reload or reconnect (KEPT).** `hello{threadId:T}` where `T` is held by this user → `claim` =
`KEPT` → session marked connected, its reaper cancelled → `on_arrival` returns on the first
line (this decides nothing) → `session.ready{restored:true}` queued at the front, ahead of the
frames the old socket never took → `restore` replays transcript, elements, a live ask with
**what is left** of its deadline → `on_ready` starts nothing. `pageLoad` decides only how much
the replay rebuilds. **The hooks do not run again**: a session survives F5 whole, `user_session`
and all, and `on_chat_start` / `on_chat_resume` fire only when there is no live session — a
fresh chat, or a resume after the reaper.

**Resume of a stored thread (cold).** Reachable only when nobody is in `T`: the reaper came, or
the user pressed "New chat". `hello{threadId:T}` → `claim` = `CREATED`, `requested_thread_id =
T` → `on_arrival` → `_claim_transit` (no record) → `_resume`: load `ThreadDetail`. A miss never
produces an error frame — the thread does not exist, or is someone else's, and either way
`_disown_thread` gives the session a fresh id and a fresh writer; the client learns from
`session.ready.thread_id` naming a different thread than it asked for. Otherwise
`hide_resume_deleted`, the panel's rows split out of `detail.elements` (see below), state and
profile from metadata, transcript loaded, `session.sidebar` rebuilt from `metadata["__sidebar"]`
plus those rows, `first_interaction = "resume"`, `resumed_thread_id = T`, `chat_started = True`,
`writer.open_gate()` → `session.ready` → `restore` sends `thread.resume` as a snapshot, then the
panel's elements and `sidebar.state` → `on_ready` launches `on_chat_resume` followed by
`on_thread_ready` in its own slot (the second runs even if the first raised).

**The element panel across a cold resume.** Restoring the panel is the engine's job; an
application that had to replay a recipe of its own in `on_chat_resume` was remembering what the
server forgot. `cl.Sidebar.set_slot(..., persist=True)` (the default) sends each element with
`for_id=None`, so the row lands in `elements` with `forId NULL` — which is exactly what
`_resume` splits on. Those rows are **removed from `detail.elements` before** `_transcript_of`
and `_thread_dict` run, or a fifty-card panel would go out twice: once inside `thread.resume`
and again as the upserts `restore()` sends ahead of `sidebar.state`. What the panel _is_ lives
in thread metadata under `__sidebar` — the `sidebar.state` frame minus `rev` (a session's
counter) and minus the `preview` slot (a borrowed element of the feed) — and
`ws/sidebar.state_from_meta` reads it back: a slot keeps only the ids it has rows for, an empty
slot is dropped, a row nothing names is ignored, `rev` starts at 0. Only the cold path rebuilds;
a `KEPT` session keeps its live model, so an F5 is unchanged.

**Thread held by another user.** `claim` = `CREATED` and the claim names the tenant, so nothing
is requested: the session is minted on a thread of its own, the database is never consulted,
and the stranger's session is not touched. With authentication off `is_owned_by` is true of
every pair, so the uuid4 in the URL is the whole capability — like a share link.

**Profile handoff.** `emitter.set_chat_profile` mints the successor's **thread**, parks the
transit record under it and sends `session.handoff{nextThreadId}`. The browser navigates there;
the arriving session finds the record in `_claim_transit`, which is what identifies the arrival
as a handover — so `_resume` is skipped and the freshly minted thread is not looked up and
disowned.

**Takeover by a second tab.** Two tabs on one URL is a takeover by design. The new socket adopts
the session, the old writer is detached, and the old connection is closed 4409 (`SUPERSEDED`)
from inside the new handler's task group; the client treats 4409 as terminal and does not
reconnect. The old handler's loops see `connection.current is False` at their next await and
leave without touching the session — its `finally` block is entirely conditional on
`connection.current`. There is no sweep: a thread holds one session, so there is nothing to
evict.

**"New chat" (`session.clear`), and the end of a chat.** `runner.release`: `relinquish`
(discard the entry) → **the end-of-chat step** — `on_chat_end`, then the `PatchThread` carrying
`persist.thread_state` — → `teardown` (cancel work, end any ask and write the interrupted-ask
row, close the writer, discard files, `outbound.abort(1000, "released")`). Discarding first
frees the thread in the same turn, so reopening it from the history resumes it from the database
instead of being handed the blank session that was sitting on it, and the boolean `relinquish`
returns is the guard that makes the end-of-chat step happen **exactly once** across the reaper,
`session.clear` and a deleted thread. `on_disconnect` schedules no reaper for a session the
registry no longer holds.

Three consequences worth stating plainly. `on_chat_end` is about the **conversation**, not the
socket: it does not run on an F5, a blip or a second tab, and an abandoned chat hears it at the
reaper (+300s) rather than at the drop. The metadata patch is the **only** write of
end-of-session `user_session` state — `persist.open_thread`'s patch fires once, at the first
interaction — so it has to happen before `teardown` clears `session.writer`; in `on_disconnect`
it ran after and was silently skipped. And `on_disconnect` keeps a copy of the patch as
insurance on any drop, which is cheap and costs a session nothing.

The release from `session.clear` is **scheduled, not awaited** (`Session.release_soon` →
`ApplicationRunner.release_soon`): the reader is a child of `_serve`'s task group, and a
teardown awaited there drains the database writer inside a scope the heartbeat can cancel — one
unanswered probe and `aclose` dies mid-flush with rows still in it, and `discard_files` and the
abort never run. Only the `relinquish` happens in the reader's own breath. `_reap` and
`DELETE /project/thread` keep the awaitable `release`: the second needs the drain finished
before it removes the rows.

The close code is **1000**, not `CloseCode.INTERNAL`: nothing failed, the conversation was given
up on purpose, and a client still listening should be free to reconnect into a fresh chat rather
than be told the server broke. `CloseCode` stays private-use (4000–4999) and gains nothing.

**Deleting a thread.** `DELETE /project/thread` releases the live session in that thread before
removing the rows — the teardown drains a writer that may still have something to file, and a
session left running would re-create the row the user just deleted.

**Heartbeat timeout.** `_heartbeat` wakes every `HEARTBEAT_INTERVAL_MS = 20_000` ms, checks
`connection.current`, sends `hb{seq}`, sleeps again, and if `last_ack != seq` calls
`outbound.drop(4408)` and cancels the group. `drop`, not `abort`: the socket is finished, the
conversation is not, and the queue is kept for the reconnect.

---

## 4. Wire protocol

Full map in **`backend/chainlit/protocol/README.md`**. 24 server tags, 6 client tags, msgspec
tagged unions discriminated on `t`, JSON text frames only (no binary branch — audio is gone,
files are HTTP).

Invariants:

- **`session.ready` is always the first frame** on an accepted socket, and it is the frame the
  client flushes its outbound buffer on. Nothing may be sent before it — which is why
  `on_arrival` may change state but must not send. It carries both ids: the thread the session
  ended up in (which may not be the one asked for) and the handle the HTTP routes are addressed
  with.
- **`hello` carries no session id.** The thread is the only identity a client may offer, and
  every field of `hello` is optional.
- **Upserts are idempotent**; `step.update` carries a `StepPatch` where absent means "no
  opinion". A duplicate frame after a mid-write socket loss is harmless.
- **Failures are addressed.** `error{code,message}` leaves the socket open (`ErrorCode`:
  `bad_message`, `unknown_tag`, `ask_slot_busy`, …). A failure that must also
  close sends a `CloseCode` too: 4400 bad handshake, 4401 unauthenticated, 4408 heartbeat
  timeout, 4409 superseded, 4413 frame too large, 4429 backlog exceeded, 4500 internal. 4429
  must be retried by the client; 4409 and 4401 are terminal and must **not** be. 4403 and 4404
  are retired — a refusal about a thread was an answer about a row that exists, and the server
  now answers with a thread of the caller's own instead.
- **4401 is sent by the auth middleware, not the handler.** `WebSocketAwareJWTCookieMiddleware`
  (`security.py`) accepts the upgrade itself and then closes it, because a refusal before an
  accept is an HTTP 403 by the ASGI spec and the browser's WebSocket API never exposes a status:
  the tab would see a bare 1006 with `opened: false`, the same thing an unreachable server looks
  like, and reconnect forever. The route handler never runs for a refused credential, so nothing
  in `ws/` has to answer for one. Only `NotAuthorizedException` is answered this way; any other
  failure still propagates and closes 4500.
- **`account.badge{count}` is pushed, never polled.** The only frame with no request behind it
  at all: it is offered after every `session.ready`, after `GET /project/account`, and on
  `emitter.refresh_account_badge()`, each time to every live session of the user. No
  `@cl.on_account_badge` hook means the frame never exists, and the client renders nothing
  rather than a zero it was not told.
- **`hb` / `hb.ack` are per connection**, never per session: the ack is recorded on
  `Connection.last_ack` and never reaches `_dispatch`.
- Unknown _fields_ are ignored (forward compatibility); an unknown _tag_, a wrong type or a
  missing required field is rejected. `MAX_FRAME_BYTES` is 8 MiB in both directions.

---

## 5. Persistence

`Persistence` (`persistence/config.py`) holds the advanced-alchemy `SQLAlchemyAsyncConfig`, an
optional blob `storage` client, and the five service classes. `Persistence.uow()` yields a
`UnitOfWork` — one `AsyncSession` plus `users`/`threads`/`steps`/`elements`/`feedbacks`. Passed
a session it borrows it (a handler's injected one, committed by the before-send handler);
standalone it opens, commits, and always returns the connection — rollback and close run
shielded so a cancelled task cannot bleed a pool connection. `isolated()` wraps DB work called
from inside the websocket's anyio task group, where a cancel scope re-delivers cancellation at
every await and asyncpg cannot survive it.

Route handlers name one service and get it injected against the request session
(`Persistence.dependencies()`). The before-send handler is `autocommit_include_redirects`:
the OAuth callback answers 302 and its user row must still commit.

**`SessionWriter`** (`persistence/writer.py`) is **one ordered writer per session**, not per
thread. A thread holds one session, but a successor can start on it while its predecessor's
writer is still draining (the window between `release`'s discard and its `aclose` — wider now
that `session.clear` schedules the release instead of awaiting it), so `WriterRegistry` keeps a
_set_ per thread and FIFO stays a per-writer promise. It queues
`SaveStep`, `DeleteStep`, `SaveElement`, `DeleteElement`, `PatchThread`; the consumer takes up
to `BATCH_LIMIT = 256` ops per transaction and replays op-by-op if the batch fails.
`hold_until_interaction=True` keeps ops (and _un-started_ uploads) in an ordered held list;
`open_gate(prelude)` releases them behind the `PatchThread` that names and attributes the row.
A session that closes before its first interaction discards them — and has uploaded nothing.
`drain()` is a fence ("everything issued before I was called has landed"), not "the queue is
empty". `WriterRegistry` is keyed by **thread**, because readers are: `drain_thread` waits for
every writer on a thread.

**How a `cl.*` write reaches the DB.** `Message.send()` → `context.emitter.send_step(dict)`
(frame + transcript entry) and `persist.save_step(dict)` → `writer_of()` → `msgspec.convert` to
`StepRecord` → `writer.submit(SaveStep(...))` → batch → `uow.steps.save`. Elements go through
`Element._create` → `persist.save_element`, which hands the writer a _callable_ upload; the row
is written from whatever record the upload returns, and a failed upload writes no row.
`persist.open_thread` is the one seam between the two halves: it announces
`thread.first_interaction` on the wire, looks up the user row id, then opens the writer gate.
`persist.thread_state` is what gets stored as thread metadata (volatile keys such as
`transit_message` are excluded) and it always carries the panel under `__sidebar`.

**The element panel's writes.** A slot's elements are ordinary element rows with `forId NULL`
(`persist.save_element`, same FIFO as steps). The row id is the engine's, not the application's:
`elements.id` is the table's only key, so `set_slot` mints it from the thread and the id the
application chose (`ws/sidebar.row_id`) — stable across calls, so a refresh updates in place, and
a different row in every conversation, so a name like `"cards"` is not one row every user
overwrites. `persist=False` fills a slot with something not worth a row; the slot remembers that
(`SidebarSlot.persisted`), and that flag — never the id's spelling — is what decides whether
the record names the slot, whether a delete is issued for what leaves it, and whether
`PUT /project/element` writes a row for an element in it. A persisted element may not carry an
`object_key` of its own: deleting a panel row discards the blob its key names, and that would be
the application's file — a file the application owns goes in by `url=`. Every id a slot lets go
— displaced, closed, cleared, or closed by the user — passes `ws/sidebar.orphaned` (an element
another slot or a step still shows is left alone) and then gets **both** `element.remove` on
the wire and `DeleteElement` to the writer, via `ws/sidebar.release`. Every **structural**
change also submits an immediate `PatchThread(metadata={"__sidebar": …})` after those deletes.
The rows are not strictly ordered against it: an element with a blob — and a custom element's
props are one — is written only once its upload finishes, so the record can name a row that lands
a moment later, which `state_from_meta` tolerates by dropping an id with no row. What the writer
does guard is the other order: a `DeleteElement` issued while that upload is in flight dooms it
(`_Uploading`), so no row lands after the delete meant to undo it and the blob it uploaded is
discarded. Scalar moves (`hide`/`show`/`activate`, and the `sidebar.user` ops that mirror them)
write nothing: a click must not cost a database write, and a `visible` lost to a crash costs one
chevron. The key stays out of the application's reach through one set,
`persist.ENGINE_METADATA_KEYS`: `thread_state` writes those keys from the engine's own state and
never from `session.state`, `split_engine_metadata` takes them out of a stored thread before
`_resume` hands the rest to `session.state`, the hooks and the snapshot, and the share route
keeps them off a public thread. A delete from the browser (`DELETE /project/element`) goes the
other way through `Session.forget_element`: the row is gone, so the transcript and the panel
let go of their copies and the panel's new shape is written down — a reload and a cold resume
must not disagree about a card the user deleted.

**`users.account`.** One `jsonb` column, `NOT NULL DEFAULT '{}'`, added by revision 0004 —
what `@cl.account` stores. Not a key in `users.metadata`: `upsert_user` replaces that column
wholesale at every sign-in (`set_={"metadata": excluded.metadata}`), so anything the engine
kept there would be gone by the next login, and a reserved-key filter on top of that would be
a patch around the wrong storage. `UserService.get_account` / `set_account` are one statement
each; the write is an `INSERT ... ON CONFLICT (identifier) DO UPDATE SET account =
excluded.account`, so a user whose row no login has written yet gets one here — and `metadata`
is not in the conflict clause, for the same reason.

**Schema and migrations.** Schema `chainlit`, mapped to the deployed layout: lowercase tables,
quoted camelCase columns, native `uuid` keys, timestamps as ISO **text** with a trailing `Z`.
Migrations live in `persistence/migrations/versions/` (four revisions) and run via
`LITESTAR_APP=your_module:app litestar database upgrade` — the `database` command group exists
because `ChainlitPlugin` registers `Persistence.plugin()`.

---

## 6. Auth

`ChainlitAuth` (`security.py`) is `JWTCookieAuth[Identity, Token]` with two defaults filled in:
`retrieve_user_handler = identity_from_token` and `key = "access_token"`. `identity_from_token`
trusts the signed token and does no database lookup — `sub` is the identifier,
`display_name`/`metadata` ride in `extras`. `Identity` is what `connection.user` holds.
`authentication_middleware_class` is `WebSocketAwareJWTCookieMiddleware`: on HTTP it is the
stock `JWTCookieAuthenticationMiddleware`, and on a websocket it accepts the upgrade and then
closes it with 4401 rather than letting the exception middleware refuse it pre-accept, which
uvicorn is obliged to turn into an HTTP 403 the browser cannot read (see §4).

`chainlit_auth()` reads the deployment settings at call time: `CHAINLIT_AUTH_SECRET`,
`CHAINLIT_AUTH_COOKIE_NAME`, `CHAINLIT_AUTH_COOKIE_PATH`, `CHAINLIT_COOKIE_SAMESITE`
(`none` forces `secure`). `ChainlitPlugin(auth=...)` accepts an instance, `None` (no middleware
at all), or the default `Empty` — meaning "on exactly when a secret is in the environment".
Because it is middleware and not a dependency, it also populates the **websocket** scope; the
browser cannot set an `Authorization` header on an upgrade, so the cookie is the only carrier.

`AuthController` (`controllers/auth.py`) serves `/auth/config`, `POST /login` (password and
direct-grant), `POST /auth/jwt`, `POST /logout`, `GET /auth/oauth/{provider}` plus
`/register`, `/vk`, `/yandex` and `/callback`, and `GET /user`. `POST /set-session-cookie` is
gone: it pinned a session id for load-balancer affinity, and the client has no id to pin — the
server mints it and names it only after the socket is open. A multi-worker deployment needs
affinity from the balancer.
Every route except `/user` carries `opt={"exclude_from_auth": True}` (`PUBLIC`) — an _opt key_,
not a handler parameter. On an excluded route the middleware never ran, so `request.user`
**raises**; those handlers read the scope through `controllers/caller.py` instead.
`assert_session_owner` answers 404, never 403, so a stranger holding a session id cannot learn
that it is live. Providers and token exchanges live in `oauth_providers.py`; state rides in a
3-minute `oauth_state` cookie. `POST /auth/header` and the Azure AD hybrid callback are not ported.

---

## 7. Testing map

`cd backend && uv run pytest`. Everything except `tests/persistence` runs with no services.

| Suite                | What it pins                                                                                                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/ws/`          | `test_registry` (the thread key, the claim, the protection queries), `test_handshake` (arrive/restore), `test_outbound` (queue, backlog, close semantics), `test_connection` (the route itself, plus every live-uvicorn case). |
| `tests/socketspec/`  | The scenario table: behaviour stated transport-free, driven against real objects.                                                                                                                                              |
| `tests/protocol/`    | Round-trip, unions, patch semantics, package independence, and `test_coverage.py`.                                                                                                                                             |
| `tests/controllers/` | `test_auth`, `test_project`, `test_files`, `test_account`, `test_account_actions` against the controllers.                                                                                                                     |
| `tests/app/`         | The plugin as assembled: `test_plugin`, `test_auth`, `test_public`, `test_spa`, `test_transit_store`. Uses a fixture `frontend_dir`, because `frontend/dist` is a build artefact.                                              |
| `tests/persistence/` | Services, statements, migrations, writer, pagination, storage backends. **Needs PostgreSQL.**                                                                                                                                  |
| `tests/test_*.py`    | The `cl.*` API surface, config, CLI, callbacks, import hygiene, and `test_account_badge` (the fan-out through the real registry, and a badge hook that raises not taking the socket down).                                     |

**`tests/socketspec`** — `cases/*.py` groups scenarios by behaviour family (ask, handshake,
bystanders — now "other conversations" — transcript, orphans, parents, reload, resume_delete,
resync) and `cases/__init__.py`
unions them into `SCENARIOS`. To add a case: append a `Scenario(name, why, given=Given(...),
when=(Incoming(...),), expect=(Expect(tag, fields),), forbid=(...), then=lambda result: ...)`
to the matching family. `given` states facts about the conversation, never about a transport.
`test_vocabulary.py` refuses any tag that is not a real `ServerMsg`/`ClientMsg` branch and any
field name the branch does not put on the wire. `test_spec.py` runs the table against every
registered driver; a row in a driver's `KNOWN_BUGS` becomes a **strict** xfail, and a
`superseded` row is skipped with the reversal as its reason.

**`tests/persistence`** connects to `TEST_DATABASE_URL`, else to the database
`chainlit_pytest` at `postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/`; if nothing is
listening it exits with the `docker run` that would start one. The session fixture runs
`DROP SCHEMA chainlit CASCADE` and re-migrates; the per-test fixture `TRUNCATE`s every table
with `RESTART IDENTITY CASCADE`. **Never point this at a database an application uses** — that
mistake has already destroyed a consumer's dev data twice.

**`tests/protocol/test_coverage.py`** parses every socket.io event name out of the pinned legacy
vocabulary and requires each to be mapped to a tag or listed in `INTENTIONALLY_DROPPED` with a
reason. A tag cannot be lost silently.

**`tests/test_import_hygiene.py`** walks the package AST and fails on a direct import of
`fastapi`, `starlette`, `pydantic`, `pydantic_settings`, `dataclasses_json`, `lazify`, `syncer`,
`asyncer`, `socketio`, `literalai` — with a self-check that the walk actually sees the package.

**The live-uvicorn tests** (`-k live`, all parametrized over
`("websockets", "websockets-sansio")`) cover the takeover, the superseded probe, the reload
that keeps its question, the reload on a greeting that keeps its whole session, the second tab
that takes an idle thread over, the second tab that is shown the first's open question, and
`session.clear` giving the conversation up. They exist because Litestar's in-process test client
never awaits a closing handshake: the superseded handler always unwinds in the harmless order,
and the old in-memory takeover test was green against code that reaped live sessions on every
profile change. **Rule: any transport change needs at least one live-server test.**

---

## 8. Invariants and traps

- **No compatibility shims** for the pre-rebuild API — `BaseDataLayer`, `mount_chainlit`,
  `server_route`, `cl.run_sync`, socket.io. They were deleted, not preserved.
- **The raw `@websocket` handler is the only duplex option** in Litestar 2.24. Verified against
  installed source and live spikes; do not "adopt more Litestar" inside `chainlit/ws`.
- **`except*`, not `except`,** around an anyio task group: it wraps even a single child
  exception, so a plain `except WebSocketDisconnect` is unreachable and a closed tab is reported
  as a 500.
- **Websocket exceptions never reach `exception_handlers`.** Litestar reads `exc.code` off a
  `WebSocketException` and closes 4500 for anything else; a task (the writer) reaches no
  middleware at all, so `Outbound` closes the socket itself.
- **msgspec `omit_defaults`**: an absent field means its default. That is why records use
  `UNSET` for "not provided" and why `step.update` carries a `StepPatch` — `false` and "no
  opinion" are different instructions.
- **`Outbound.send` never blocks, awaits or raises.** It returns `False` when refused. A full
  backlog closes the connection (4429) rather than dropping a delta.
- **The writer task must not be bound to a `yield` dependency.** `Outbound` belongs to the
  session, the writer task to the socket; teardown-on-disconnect would throw the backlog away on
  every blip. The same shape trap applies to `WriterRegistry`: the shutdown drain must **not**
  go in `on_shutdown`, which unwinds _after_ the SQLAlchemy plugin disposes the engine.
- **`session.state` is application state, not a bus.** It is persisted into thread metadata and
  read back on resume. Handshake decisions travel on the typed `Arrival`, never as string keys
  in that dict.
- **Only `ApplicationRunner._bind` sets the context var.** A callback that finds no context was
  launched from the wrong place.
- **`on_arrival` may not send anything**; `on_ready` may not decide anything.
- **Only the current connection tears anything down** (`connection.current`).
- **The registry decides; the caller does.** Nothing in `ws/registry.py` tears a session down.
  `ApplicationRunner.release` is the one place that discards an entry and tears down what was in
  it, and it discards **first** so the thread is free in the same turn.
- **A teardown never runs inside a connection's task group.** `_serve`'s group cancels every
  child when one fails, and a cancelled `SessionWriter.aclose` loses the rows it was flushing.
  The reaper has always been a plain `asyncio` task; `session.clear` schedules one too
  (`release_soon`).
- **The chat ends once, in `release`.** `relinquish` returning `True` is the guard — the
  registry entry _is_ the fact "this conversation is still going", so no separate flag exists to
  fall out of step. `on_chat_end` is never driven by a socket closing.
- **One session per thread, enforced by the key.** `register` raises `ThreadHeld` rather than
  overwriting, and nothing may `await` between `claim` and the `register` that acts on it.
- **There is no fan-out.** Both protection queries are a single lookup by thread.
