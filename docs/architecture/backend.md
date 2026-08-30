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

| Path                                                                                              | Purpose                                                                                                         | Surface                |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `__init__.py`                                                                                     | Re-exports the `cl.*` API; loads `.env` before any other import.                                                | public                 |
| `plugin.py`                                                                                       | `ChainlitPlugin(InitPlugin)` — the entire integration surface with a host `Litestar`.                           | public (embedding)     |
| `runner.py`                                                                                       | `ApplicationRunner`: runs `config.code` on behalf of sessions; the only place that sets the context var.        | internal               |
| `ws/session.py`                                                                                   | `Session` — a conversation, independent of the socket carrying it. Imports nothing from the `cl.*` layer.       | internal               |
| `ws/connection.py`                                                                                | The `@websocket("/ws")` route, `Connection`, the reader and heartbeat loops.                                    | internal               |
| `ws/outbound.py`                                                                                  | `Outbound` — one bounded queue, one writer task, one owner of the close.                                        | internal               |
| `ws/registry.py`                                                                                  | `SessionRegistry` plus the eviction/ownership predicates. Imports nothing from `chainlit` or the transport.     | internal               |
| `ws/handshake.py`                                                                                 | `arrive` / `ready_frame` / `restore` / `sweep_superseded` — what a `hello` is allowed to mean.                  | internal               |
| `protocol/{server,client,payloads,codec}.py`                                                      | msgspec tagged unions on `t`, plus `CloseCode`/`ErrorCode`. Imports no other `chainlit` module.                 | internal (stable wire) |
| `protocol/README.md`                                                                              | Old-event → tag map, renames, shape changes, close codes.                                                       | docs                   |
| `controllers/auth.py`                                                                             | `/auth/*`, `/login`, `/logout`, `/user`, `/set-session-cookie`.                                                 | internal               |
| `controllers/project.py`                                                                          | Threads, elements, feedback, actions, `/project/settings`, `/health`.                                           | internal               |
| `controllers/files.py`                                                                            | Upload/download, `/favicon`, `/logo`, `/avatars/*`.                                                             | internal               |
| `controllers/index.py`                                                                            | `render_index` — fills the built SPA shell with title, favicon, OG tags, theme.                                 | internal               |
| `controllers/sessions.py`                                                                         | `LiveSession` / `SessionRegistry` protocols the routes are allowed to see.                                      | internal               |
| `controllers/caller.py`                                                                           | `caller`, `caller_identifier`, `assert_session_owner` — reading the scope safely.                               | internal               |
| `persistence/`                                                                                    | `records` → `models` → `statements` → `repositories`/`services` → `config` → `writer`.                          | internal               |
| `persist.py`                                                                                      | The `cl.*` → rows seam: `save_step`, `save_element`, `delete_*`, `open_thread`, `thread_state`.                 | internal               |
| `security.py`                                                                                     | `ChainlitAuth(JWTCookieAuth)`, `Identity`, `identity_from_token`, `chainlit_auth()`.                            | internal               |
| `oauth_providers.py`                                                                              | The configured OAuth providers and their token exchanges.                                                       | internal               |
| `transit_store.py`                                                                                | `TransitStore` — the TTL'd one-shot profile-switch handover on a `litestar.stores` store.                       | internal               |
| `config.py`                                                                                       | `.chainlit/config.toml` decoded with msgspec, plus `config.code` (the registered callbacks).                    | internal               |
| `callbacks.py`                                                                                    | The `@cl.on_*` decorators; each stores a wrapped function on `config.code`.                                     | public                 |
| `context.py`                                                                                      | `ChainlitContext`, `context_var`, `init_context`, and the `cl.context` proxy.                                   | public (`cl.context`)  |
| `emitter.py`                                                                                      | `Emitter` — one method per thing the app can put on screen; produces frames, never rows.                        | internal               |
| `message.py`                                                                                      | `Message`, `ErrorMessage`, `AskUserMessage`, `AskActionMessage`, `AskFileMessage`, `AskElementMessage`.         | public                 |
| `step.py`                                                                                         | `Step` and the `@cl.step` decorator.                                                                            | public                 |
| `element.py`                                                                                      | `Image`, `Pdf`, `Text`, `File`, `Video`, `Audio`, `Plotly`, `Pyplot`, `Dataframe`, `CustomElement`, `TaskList`. | public                 |
| `action.py`                                                                                       | `Action` — a button attached to a message.                                                                      | public                 |
| `input_widget.py`                                                                                 | Input widget dataclasses (`cl.input_widget`).                                                                   | public                 |
| `user_session.py`                                                                                 | `cl.user_session` — a thin view over `Session.state`.                                                           | public                 |
| `chat_context.py`                                                                                 | `cl.chat_context` — the conversation's messages, kept on the session's state.                                   | public                 |
| `sidebar.py`, `mode.py`, `types.py`, `user.py`                                                    | `ElementSidebar`, `Mode`/`ModeOption`, `ThreadDict`/`ChatProfile`/`Starter`, `User`/`PersistedUser`.            | public                 |
| `cli/__init__.py`                                                                                 | The `chainlit` command: `run`, `hello`, `init`, `create-secret`, `lint-translations`.                           | public (CLI)           |
| `utils.py`, `_utils.py`, `secret.py`, `markdown.py`, `logger.py`, `translations.py`, `version.py` | Helpers, secret generation, `chainlit.md` bootstrap, the `chainlit` logger, translation linting, `__version__`. | internal               |
| `frontend/dist`, `copilot/dist`, `translations/*.json`, `sample/`                                 | Built JS artefacts (not in git), shipped UI translations, the `chainlit hello` demo apps.                       | assets                 |

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
- **Dependencies** — `sessions`, `persistence_enabled`, `security`, `user_service`, all with
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

**Handshake.** Accept → `_first_hello` (10s deadline, must be a well-formed `hello`, else close
4400/4413) → `arrive(...)` → optional refusal (close 4403) → `on_arrival` → `Connection` →
`_take_over` → `_serve`.

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

**`SessionRegistry.claim`** returns one of four outcomes — the vocabulary the scenario table
uses: `REFUSED` (ownership check first, on both paths), `KEPT` (not a page load, or a page load
onto a session with live work), `REPLACED` (a page load onto an idle session), `CREATED`.
`has_live_work` = live ask **or** live task **or** parked reply.

**`ApplicationRunner`** is the application half: `make_session` (mints a thread id and, with
persistence, a `SessionWriter(hold_until_interaction=True)`), `on_arrival` — the **one** place
that decides start / resume / nothing — `on_ready` (which only carries out what `on_arrival`
decided), `on_disconnect`, `_reap`, `teardown`, `on_message`, `on_stop`, `call_action`.
`requested_thread_id` is what the client _asked_ to resume; `thread_id` may be the id the
session was minted with, and only an asked-for thread can be reported missing.

### Sequences

**Fresh page load.** `hello{pageLoad:true}` → `claim` = `CREATED` → `make_session` → registered
→ `on_arrival`: no supersession, `_resume` returns false (nothing requested), `_claim_transit`,
`chat_started = True`, `start_chat = True` → `session.ready{restored:false}` → `restore` sends
nothing but `task.indicator{running:false}` → `on_ready` launches `on_chat_start` as the
session's `current_task`.

**Reconnect (KEPT).** `hello{pageLoad:false}` → `claim` = `KEPT` → session marked connected, its
reaper cancelled → `on_arrival` returns on the first line (a reconnect decides nothing) →
`session.ready{restored:true}` queued at the front, ahead of the frames the old socket never
took → `restore` replays transcript, elements, a live ask with **what is left** of its deadline
→ `on_ready` starts nothing.

**Reload onto an idle session (REPLACED).** `hello{pageLoad:true}`, held session has no ask, no
task, no parked reply → `claim` = `REPLACED`; the old entry is discarded and carried on
`Arrival.superseded` → new session created under the same id → `on_arrival` tears the
superseded session down (`teardown`: cancel work, end any ask, close the writer, discard files,
`outbound.abort()`) → then the normal fresh-load branch.

**Resume of a stored thread.** `hello{threadId:T}` → `requested_thread_id = T` → `on_arrival` →
`_resume`: load `ThreadDetail`; missing or not this user's → `arrival.missing_thread = T` (and,
if it is someone else's, `_disown_thread` gives the session a fresh id and a fresh writer);
otherwise `hide_resume_deleted`, state and profile from metadata, transcript loaded,
`first_interaction = "resume"`, `resumed_thread_id = T`, `chat_started = True`,
`writer.open_gate()` → `session.ready` → `restore` sends `thread.resume` as a snapshot →
`on_ready` sends `error{thread_not_found}` if applicable, then launches `on_chat_resume`
followed by `on_thread_ready` in its own slot (the second runs even if the first raised).

**Takeover by a second tab.** The new socket adopts the session, the old writer is detached, and
the old connection is closed 4409 (`SUPERSEDED`) from inside the new handler's task group. The
old handler's loops see `connection.current is False` at their next await and leave without
touching the session — its `finally` block is entirely conditional on `connection.current`.
Separately, `sweep_superseded` evicts sessions of the _same thread_ that are disconnected and
parked on a question; a running task is deliberately not a shield.

**Heartbeat timeout.** `_heartbeat` wakes every `HEARTBEAT_INTERVAL_MS = 20_000` ms, checks
`connection.current`, sends `hb{seq}`, sleeps again, and if `last_ack != seq` calls
`outbound.drop(4408)` and cancels the group. `drop`, not `abort`: the socket is finished, the
conversation is not, and the queue is kept for the reconnect.

---

## 4. Wire protocol

Full map in **`backend/chainlit/protocol/README.md`**. 23 server tags, 6 client tags, msgspec
tagged unions discriminated on `t`, JSON text frames only (no binary branch — audio is gone,
files are HTTP).

Invariants:

- **`session.ready` is always the first frame** on an accepted socket, and it is the frame the
  client flushes its outbound buffer on. Nothing may be sent before it — which is why
  `on_arrival` may change state but must not send.
- **Upserts are idempotent**; `step.update` carries a `StepPatch` where absent means "no
  opinion". A duplicate frame after a mid-write socket loss is harmless.
- **Failures are addressed.** `error{code,message}` leaves the socket open (`ErrorCode`:
  `bad_message`, `unknown_tag`, `thread_not_found`, `ask_slot_busy`, …). A failure that must also
  close sends a `CloseCode` too: 4400 bad handshake, 4401 unauthenticated, 4403 session
  forbidden, 4404 thread forbidden, 4408 heartbeat timeout, 4409 superseded, 4413 frame too
  large, 4429 backlog exceeded, 4500 internal. 4429 must be retried by the client.
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
thread — two tabs on one thread are two writers, and FIFO is a per-writer promise. It queues
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
`transit_message` are excluded).

**Schema and migrations.** Schema `chainlit`, mapped to the deployed layout: lowercase tables,
quoted camelCase columns, native `uuid` keys, timestamps as ISO **text** with a trailing `Z`.
Migrations live in `persistence/migrations/versions/` (three revisions) and run via
`LITESTAR_APP=your_module:app litestar database upgrade` — the `database` command group exists
because `ChainlitPlugin` registers `Persistence.plugin()`.

---

## 6. Auth

`ChainlitAuth` (`security.py`) is `JWTCookieAuth[Identity, Token]` with two defaults filled in:
`retrieve_user_handler = identity_from_token` and `key = "access_token"`. `identity_from_token`
trusts the signed token and does no database lookup — `sub` is the identifier,
`display_name`/`metadata` ride in `extras`. `Identity` is what `connection.user` holds.

`chainlit_auth()` reads the deployment settings at call time: `CHAINLIT_AUTH_SECRET`,
`CHAINLIT_AUTH_COOKIE_NAME`, `CHAINLIT_AUTH_COOKIE_PATH`, `CHAINLIT_COOKIE_SAMESITE`
(`none` forces `secure`). `ChainlitPlugin(auth=...)` accepts an instance, `None` (no middleware
at all), or the default `Empty` — meaning "on exactly when a secret is in the environment".
Because it is middleware and not a dependency, it also populates the **websocket** scope; the
browser cannot set an `Authorization` header on an upgrade, so the cookie is the only carrier.

`AuthController` (`controllers/auth.py`) serves `/auth/config`, `POST /login` (password and
direct-grant), `POST /auth/jwt`, `POST /logout`, `GET /auth/oauth/{provider}` plus
`/register`, `/vk`, `/yandex` and `/callback`, `GET /user`, and `POST /set-session-cookie`.
Every route except `/user` carries `opt={"exclude_from_auth": True}` (`PUBLIC`) — an _opt key_,
not a handler parameter. On an excluded route the middleware never ran, so `request.user`
**raises**; those handlers read the scope through `controllers/caller.py` instead.
`assert_session_owner` answers 404, never 403, so a stranger holding a session id cannot learn
that it is live. Providers and token exchanges live in `oauth_providers.py`; state rides in a
3-minute `oauth_state` cookie. `POST /auth/header` and the Azure AD hybrid callback are not ported.

---

## 7. Testing map

`cd backend && uv run pytest`. Everything except `tests/persistence` runs with no services.

| Suite                | What it pins                                                                                                                                                                      |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/ws/`          | `test_registry` (claim/eviction predicates), `test_handshake` (arrive/restore), `test_outbound` (queue, backlog, close semantics), `test_connection` (the route itself).          |
| `tests/socketspec/`  | The scenario table: behaviour stated transport-free, driven against real objects.                                                                                                 |
| `tests/protocol/`    | Round-trip, unions, patch semantics, package independence, and `test_coverage.py`.                                                                                                |
| `tests/controllers/` | `test_auth`, `test_project`, `test_files` against the controllers.                                                                                                                |
| `tests/app/`         | The plugin as assembled: `test_plugin`, `test_auth`, `test_public`, `test_spa`, `test_transit_store`. Uses a fixture `frontend_dir`, because `frontend/dist` is a build artefact. |
| `tests/persistence/` | Services, statements, migrations, writer, pagination, storage backends. **Needs PostgreSQL.**                                                                                     |
| `tests/test_*.py`    | The `cl.*` API surface, config, CLI, callbacks, import hygiene.                                                                                                                   |

**`tests/socketspec`** — `cases/*.py` groups scenarios by behaviour family (ask, handshake,
bystanders, transcript, orphans, parents, reload, resume_delete, resync) and `cases/__init__.py`
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

**The two live-uvicorn tests** are `test_live_a_takeover_leaves_the_session_connected` and
`test_live_a_superseded_probe_cannot_close_the_new_socket`, both parametrized over
`("websockets", "websockets-sansio")`. They exist because Litestar's in-process test client
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
- **The registry decides; the caller does.** Nothing in `ws/registry.py` deletes a session, and
  a candidate must be re-checked with `should_evict` immediately before the awaiting delete.
- **There is no fan-out.** What looks like cross-session behaviour is a scan of the registry.
