# AGENTS.md

Guidance for AI agents and developers working in this repository. Read this
before touching anything; then read the architecture documents it links.

## 1. What this repository is

This is a hard fork of Chainlit rebuilt on **Litestar 2.24**: msgspec instead of
Pydantic, Advanced Alchemy over asyncpg/PostgreSQL instead of the pluggable data
layers, a native `@websocket` route with a typed wire protocol instead of
socket.io. It is published as the distribution **`chainlit-litestar`** while the
import name stays `chainlit`; the two distributions own the same package and must
never be installed together. **Python 3.14 only** (`requires-python = ">=3.14,<3.15"`).
The work lives on `feat/litestar-rebuild`, and the fork's single consumer is
`chainlit-panda`, a multi-profile product-search assistant in a sibling repository.

Upstream compatibility is **abandoned, not deferred**. `BaseDataLayer`,
`mount_chainlit`, `server_route`, `cl.current_user`, `cl.run_sync` and the
socket.io protocol are deleted. Do not reintroduce them, and do not write shims
for them: a shim is a promise to a caller that does not exist.

The governing design rule: for every construct, ask whether it exists _because
the backend was FastAPI_, or because Chainlit predates a better Litestar
primitive. If either — delete it, do not repackage it. Write in the 3.0-shaped
API from line one (`NamedDependency`, `FromQuery[T]`, `InitPlugin`/`CLIPlugin`,
imports from `advanced_alchemy.extensions.litestar`) so the eventual 3.0 bump is
a version pin rather than a port. `backend/tests/test_import_hygiene.py` enforces
the floor: no module under `backend/chainlit/` may import `fastapi`, `starlette`,
`pydantic`, `pydantic_settings`, `dataclasses_json`, `lazify`, `syncer`,
`asyncer`, `socketio` or `literalai`. None of them are in the lockfile any more.

Structure lives in the architecture documents, not here:

- [docs/architecture/backend.md](docs/architecture/backend.md) — package layout,
  the websocket transport, persistence, plugin and CLI.
- [docs/architecture/client.md](docs/architecture/client.md) — frontend,
  `@chainlit/react-client`, the transport owner, custom elements.

## 2. Prerequisites and commands

Python **3.14**, Node **24+** (`lts/*` in CI), [uv](https://docs.astral.sh/uv/),
pnpm **9** (pinned by `packageManager`). The repository is one uv workspace
(root `pyproject.toml`, member `backend/`) and one pnpm workspace
(`frontend/`, `libs/react-client/`, `libs/copilot/`).

### Install

| What   | Command                                                                                                                                                 | Directory |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| Python | `uv sync --all-packages --all-extras --all-groups` (without `--all-groups` the root `default-groups` drops backend's `dev` group and uninstalls `ruff`) | repo root |
| JS     | `pnpm install`                                                                                                                                          | repo root |

### Build

| What            | Command                                      | Directory  |
| --------------- | -------------------------------------------- | ---------- |
| react-client    | `pnpm --filter @chainlit/react-client build` | repo root  |
| All JS packages | `pnpm build`                                 | repo root  |
| Backend wheel   | `uv build`                                   | `backend/` |

**Build order matters**: `@chainlit/react-client` must be built before
`frontend` and `libs/copilot` type-check or build against it — CI does exactly
this in `check-frontend.yaml`. `pnpm build` is `pnpm run --recursive build`,
which respects workspace order; a bare `cd frontend && pnpm build` after editing
the client does not.

### Dev servers

| What     | Command                                           | Directory   | URL                                      |
| -------- | ------------------------------------------------- | ----------- | ---------------------------------------- |
| Backend  | `uv run chainlit run chainlit/sample/hello.py -h` | `backend/`  | http://localhost:8000                    |
| Frontend | `pnpm run dev`                                    | `frontend/` | http://localhost:5173 (proxies to :8000) |

`chainlit run` flags: `-w/--watch`, `-h/--headless` (do not open a browser),
`-d/--debug`, `-c/--ci`, `--host`, `--port`, `--root-path`, `--ssl-cert`,
`--ssl-key`. Migrations are run by Advanced Alchemy's CLI, not by a helper of
ours: `LITESTAR_APP=your_module:app litestar database upgrade`.

### Tests

| What               | Command                                                    | Directory |
| ------------------ | ---------------------------------------------------------- | --------- |
| Backend (all)      | `uv run --no-project pytest --cov=chainlit/`               | repo root |
| Backend (one file) | `uv run --no-project pytest backend/tests/test_message.py` | repo root |
| Frontend/lib unit  | `pnpm test`                                                | repo root |
| E2E (Cypress)      | `pnpm test:e2e`                                            | repo root |

`testpaths = ["backend/tests"]` and `asyncio_mode = "auto"` are set in the root
`pyproject.toml`, so pytest resolves from the repo root wherever you invoke it.
`--no-project` is the CI form: it skips the project discovery and sync a bare
`uv run` performs and just uses the existing `.venv`.

Root `pnpm test` is `pnpm run --recursive test` (vitest), **not** Cypress; E2E is
`pnpm test:e2e` (`cypress run`), and the Cypress harness starts and stops its own
`chainlit run` on port 8000.

**Persistence tests** are PostgreSQL-only and need a live server. They read
`TEST_DATABASE_URL`, defaulting to
`postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/chainlit_pytest`. Start one
with the exact command the conftest prints when nothing is listening:

```
docker run -d --name chainlit-test-pg -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=chainlit_pytest \
  -p 5432:5432 postgres:16
```

The suite builds its schema by running the real migrations, then `DROP SCHEMA
"chainlit" CASCADE` and `TRUNCATE … CASCADE` between tests. See rule 6.

**Live-server tests**: `backend/tests/ws/test_connection.py` contains
`test_live_*` cases that run against a real uvicorn, parametrized over the
`websockets` and `websockets-sansio` implementations (`--ws auto` resolves to the
latter on uvicorn 0.52, which is what the consumer's container runs). Select them
with `uv run --no-project pytest backend/tests/ws/test_connection.py -k live`.

**Bounded runs on macOS** (no GNU `timeout`) — never leave a server or a suite
unsupervised in an agent session. Prefer the tool's own timeout (the Bash
tool's `timeout` parameter, up to 10 minutes) and run the command in the
foreground. If you must bound it from the shell, use a watchdog that returns
as soon as the command finishes — **not** `sleep N` after `&`, which waits
the whole `N` even when the command took a minute (a ten-gate run lost half
an hour to it on 30.08.2026):

```
perl -e 'alarm shift; exec @ARGV' 600 uv run pytest -q
```

### Lint, format, type-check

| What                 | Command                                                                         | Directory |
| -------------------- | ------------------------------------------------------------------------------- | --------- |
| Lint JS/TS           | `pnpm lint` / `pnpm lint:fix`                                                   | repo root |
| Format JS/TS         | `pnpm format-check` / `pnpm format`                                             | repo root |
| Lint Python          | `uv run scripts/lint.py [--fix]`                                                | repo root |
| Format Python        | `uv run scripts/format.py [--check]`                                            | repo root |
| Type-check Python    | `uv run scripts/type_check.py`                                                  | repo root |
| Type-check TS        | `pnpm type-check`                                                               | repo root |
| Protocol types fresh | `uv run --no-project --directory backend scripts/gen_protocol_types.py --check` | repo root |

The Python scripts are thin wrappers: `ruff check`, `ruff format`, `mypy backend/`.
All of them accept path arguments (`uv run scripts/lint.py backend/chainlit/ws/`),
and so do `pnpm lint` and `pnpm format-check:files`. TypeScript type-checking is
per-project only.

## 3. Development rules

1. **Verify against source, never memory.** Use Context7 MCP when it is
   available (pre-resolved IDs in [docs/context7.md](docs/context7.md)); when it
   is not, read the installed library under `.venv/lib/python3.14/site-packages/`.
   _Why: this fork sits on a Litestar version whose behaviour differs from every
   blog post about it, and a wrong assumption here costs a day of debugging a
   handshake._
2. **Refute your own reading before you change anything.** A defect derived from
   static reading is a hypothesis: have a subagent try to refute it with
   file:line evidence, and gate the change on a test that fails first. _Why:
   more than one "obvious" bug in this transport turned out to be unreachable,
   and more than one "unreachable" one was live._
3. **Every new test gets a mutation check.** Break the behaviour the test
   guards, watch it go red, restore. _Why: a test that passes against broken
   code is worse than none — it is a claim of coverage._
4. **Transport changes need at least one live-server test.** The in-process
   `create_test_client` never awaits a closing handshake, so `close` is only a
   queue write and the superseded path always unwinds in the harmless order.
   _Why: the old takeover test was green against code that reaped live sessions
   on every profile change._
5. **Wire protocol changes move as one unit.** Edit the msgspec structs in
   `backend/chainlit/protocol/` (`server.py`, `client.py`, `payloads.py`,
   `codec.py`), then regenerate the TypeScript view:
   `uv run --no-project --directory backend scripts/gen_protocol_types.py`. **Never hand-edit
   `libs/react-client/src/protocol/messages.ts`** — it is generated, and CI runs
   the `--check` form. Update `backend/tests/protocol/test_coverage.py` (a
   retired name needs an entry in `INTENTIONALLY_DROPPED` with a reason) and
   `backend/tests/socketspec/` in the same change. Tags are dotted and
   noun-first (`step.upsert`, `ask.end`, `thread.resume`), discriminated on `t`.
   Frames are **JSON only** — there is no binary branch and nothing on this wire
   carries `bytes`. The canonical description is
   `backend/chainlit/protocol/README.md`. _Why: a drift between the structs and
   the client surfaces as a runtime shape mismatch in a browser, not as a build
   failure._
6. **Never point tests at a database an application uses.** The persistence
   conftest destroys the schema it connects to; the default is `chainlit_pytest`
   and the consumer's dev container must be on its own database. Before a full
   run, inspect the running app's `POSTGRES_DB` (`docker inspect chainlit-panda`)
   rather than trusting that someone switched it. _Why: this has already erased a
   live dev thread twice._
7. **Custom elements depend on the host stylesheet.** Consumer elements
   (`src/public/elements/*.jsx`) are compiled in the browser against the built
   frontend CSS, so a Tailwind utility exists for them only if the application
   happens to use it too. Layout, spacing and type utilities are therefore
   safelisted in `frontend/tailwind.config.js`. When an element needs a utility
   that is not there, **extend the safelist**; do not tell the consumer to inline
   styles. _Why: a host's card must not lose its grid because a feature that
   used `grid-cols-2` was deleted here._
8. **Do not add Litestar primitives to `chainlit/ws`.** The raw `@websocket`
   handler is the only duplex option in Litestar 2.24: `websocket_listener` is
   strictly turn-taking, `websocket_stream` is send-only and discards inbound
   frames, Channels' `Subscriber` drops frames, `litestar.events` is unordered
   fire-and-forget, and Stores cannot hold live tasks. This was settled against
   the installed source and live spikes; do not re-litigate it. _Why: this
   protocol is not turn-taking — the server talks whenever it has something to
   say, and the client talks over it._
9. **Conventional Commits, with an AI trailer.** Format
   `<type>(<scope>): <description>`; types `feat`, `fix`, `chore`, `docs`,
   `refactor`, `test`, `ci`. Every commit made with AI assistance carries a
   trailer as the last line of the body:

   ```
   Co-Authored-By: <Agent Name> <agent-email-or-noreply>
   ```

   e.g. `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`,
   `Co-Authored-By: GitHub Copilot <noreply@github.com>`,
   `Co-Authored-By: Gemini CLI <noreply@google.com>`. A husky `pre-commit` hook
   runs `lint-staged`, which lints and formats touched files and type-checks the
   projects they belong to. _Why: the hook is the only gate that runs before CI._

10. **Parallel agents: named ownership.** Each agent owns a named set of files —
    including any `__init__.py` it must touch — writes scratch to its own
    subdirectory, and never runs a repo-wide gate (`pytest` with no path,
    `type_check.py`, `lint.py --fix` with no path). The parent integrates, runs
    the full gates and commits. _Why: two agents formatting the same tree
    produce a merge conflict nobody asked for, and a shared gate reports another
    agent's half-finished work as your failure._
11. **Write in this codebase's voice.** A module docstring says _why_ the module
    exists and what it refuses to do; a comment names the bug it prevents. No
    restating of the code, no ceremonial headers. Read `chainlit/ws/__init__.py`
    or `tests/persistence/conftest.py` for the register. _Why: the reasoning is
    the part that cannot be recovered from the code later._

## 4. Release and consumer loop

Wheels come from `.github/workflows/build-litestar.yaml`, which fires on tags
matching `litestar-v*` on `feat/litestar-rebuild`, builds the JS assets, copies
them into `backend/chainlit/{frontend,copilot}/dist/`, runs the full backend
suite against a PostgreSQL service, builds the wheel and publishes it as a
GitHub **pre-release**. The workflow rewrites `backend/chainlit/version.py` from
the tag (`litestar-v3.0.0a12` → `3.0.0a12`), so the tag and the committed version
must agree.

The loop: fix → bump `backend/chainlit/version.py` → `chore(release): 3.0.0aN`
→ tag `litestar-v3.0.0aN` → push branch and tag → wait for the wheel → repin the
consumer. The consumer pins the release URL in **three** places —
`pyproject.toml`, `requirements.txt` (its Docker image installs from
requirements.txt, so pyproject alone changes nothing) and `uv.lock`.

For a frontend-only check without a release, hot-copy the built assets into the
running container:

```
docker cp frontend/dist/. <container>:/usr/local/lib/python3.14/site-packages/chainlit/frontend/dist/
```

**Never start the consumer's container with `docker compose` from an agent shell
without clearing `POSTGRES_*` first.** Compose substitutes `${POSTGRES_HOST}` and
friends from the shell before `.env`, and an agent session has been observed to
inherit the production values — the dev container then runs against the
production database (30.08.2026, three times, found only because production
lacks `threads.updatedAt`). Use
`env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_PORT docker compose -f docker-compose.dev.yml up -d --build`
and verify with `docker inspect chainlit-panda` that `POSTGRES_HOST` is
`chainlit-test-pg`. Production has never been migrated by this fork: deploying
it there needs `litestar database stamp 0001_baseline` then `upgrade` first.

The consumer's dev container runs `uvicorn --reload` under
`debugpy --wait-for-client`, so after any restart it blocks until a debugger
attaches — a "hung" container after a restart is usually this, not a crash.

## 5. Known state and gotchas

- Three vitest cases in `frontend/tests/displayModePrecedence.spec.ts` fail on a
  clean checkout; they are pre-existing, not your regression.
- `libs/copilot` type-check is a deliberate no-op (`echo 'SKIPPED: …'`), and the
  lint-staged entry for it is commented out. Rationale:
  [docs/research/copilot-type-checking.md](docs/research/copilot-type-checking.md).
- Three Cypress specs are permanently red on a ru-RU machine (locale-dependent
  assertions). Not a regression either.
- `starlette`, `fastapi`, `pydantic` and `mcp` are gone from `uv.lock` entirely.
  `test_import_hygiene.py` bans `starlette` alongside `fastapi`; older notes
  saying "starlette must stay because `mcp` pins it" are obsolete.
- The persistence suite needs `chainlit-test-pg` running. Symptom of a wedged
  Docker Desktop (seen 29.08.2026): `docker ps` hangs, port 5432 still accepts
  TCP, and every DB-backed test errors in fixture setup with `TimeoutError`
  from `asyncpg` connect. Restart Docker Desktop rather than debugging the
  suite; the container then waits for its debugger again (see section 4).

## 6. MCP-first, and documentation verification

Prefer MCP servers over manual alternatives when they are available:
**Context7** for library docs and API references, **Serena** for code navigation
and refactoring, **GitHub MCP** for issues, PRs, actions and commits. Fall back
to CLI tools, direct file reads or web search only when the corresponding MCP is
unavailable or cannot answer.

Before writing or changing code that touches a third-party API, verify the
signature against the docs — lookup order: Context7 → WebFetch → WebSearch →
installed source under `.venv`. Pre-resolved Context7 IDs live in
[docs/context7.md](docs/context7.md). **That file is stale in places**: it still
lists FastAPI, Pydantic, python-socketio, socket.io-client and SQLite/Azure
entries for stacks this fork no longer uses. Ignore those rows; it is not
maintained as part of this document.
