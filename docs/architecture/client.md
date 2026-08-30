# Client architecture

The browser side of this fork: the publishable `@chainlit/react-client` library, the
`frontend` single-page app, and the embeddable `libs/copilot` widget. socket.io is
gone; everything below speaks the msgspec-tagged JSON protocol described in
`backend/chainlit/protocol/README.md` over one native websocket.

Every claim here was read out of the source at the path named next to it.

## 1. Packages and build pipeline

`pnpm-workspace.yaml` lists three packages:

| Package                  | Path                 | Output                                      | Type-check        |
| ------------------------ | -------------------- | ------------------------------------------- | ----------------- |
| `@chainlit/react-client` | `libs/react-client/` | `dist/` via tsup (esm + cjs + `.d.ts`)      | `tsc --noemit`    |
| `@chainlit/app`          | `frontend/`          | `frontend/dist/` via Vite                   | `tsc --noemit`    |
| `@chainlit/copilot`      | `libs/copilot/`      | `libs/copilot/dist/index.js`, a single IIFE | skipped by design |

`react-client` must be built before `frontend`, and it is: `frontend/package.json`
declares `"@chainlit/react-client": "workspace:^"`, so the root
`pnpm build` (`pnpm run --recursive build`) orders the packages topologically.
`libs/copilot` depends on both `@chainlit/app` and `@chainlit/react-client` and is
therefore built last. Never run `cd frontend && pnpm build` against a stale
`libs/react-client/dist` — the app imports the built package, not the sources.

`uv build` in `backend/` runs the hatchling hook in `backend/build.py`:
`pnpm install --frozen-lockfile`, `pnpm build` at the repo root, then
`frontend/dist` → `backend/chainlit/frontend/dist` and `libs/copilot/dist` →
`backend/chainlit/copilot/dist`. Both are `exclude`d from the source tree but listed
as `artifacts` in `backend/pyproject.toml`, which is how built assets reach the wheel
uncommitted. The hook returns early when `../package.json` is absent — a wheel built
from an sdist that already carries the assets.

Copilot's `type-check` script is an `echo` pointing at
`docs/research/copilot-type-checking.md`. The reason: copilot imports frontend
**source** by deep path (`@chainlit/app/src/components/...`), and those files rely on
path aliases declared in `frontend/tsconfig.json`, not in scope under copilot's own
`tsconfig.json`. Vite resolves them anyway (`vite-tsconfig-paths` plus the `@chainlit`
alias in `libs/copilot/vite.config.ts`), so only `tsc` fails. Read the research note
before trying to "fix" it.

### Tailwind and the custom-element safelist

`frontend/tailwind.config.js` scans only `./index.html` and `./src/**/*`. Custom
elements are not in that content set: `frontend/src/components/Elements/CustomElement`
fetches an app's `public/elements/*.jsx` and compiles it in the browser with
`react-runner`, against the stylesheet the frontend already shipped. A utility a
host element uses therefore exists only if the application source happens to use it
too — and would silently vanish the day that usage is deleted.

The `safelist` block fixes that. It is a contract with host apps, not decoration.
Three patterns: browser filter utilities; a layout/spacing/type group (`grid-cols-1..12`,
`col-span-*`, `gap-*`, margins and paddings, `space-x|y-*`, the width/height scale,
`min-w|max-w`, tracking, leading, font weights, text sizes, `rounded*`, `items|self-*`,
`justify-*`, `flex-*`, `line-clamp-1..6`, `truncate`, `whitespace-*`, `overflow-*`);
and an opacity/semantic-colour group
(`opacity-0..100`, `border|text|bg-(primary|muted|accent|destructive)[-foreground][/opacity]`)
with `hover` and `disabled` variants. To extend it, add to the relevant regex, keeping
the guarantee in mind: anything a host element may use must be matched here, and
anything matched here ships in every build forever.

## 2. Transport

Two objects, in two files, with a clear split of duties.

**`libs/react-client/src/socket.ts` — `ChainlitSocket`** is one reconnecting
websocket.

- `websocketUrl(httpEndpoint)` derives `ws(s)://host/<root>/ws`; `WEBSOCKET_PATH` is
  `/ws`. Auth is cookie-only — nothing in the URL or the handshake frame — so a
  refused upgrade arrives as an HTTP 403 and the browser reports a plain 1006 with
  `opened: false`.
- On `onopen` it arms the watchdog and writes `hello()` immediately, bypassing the
  buffer. The buffer is _not_ drained until `session.ready` (socket.io drained first
  and announced later, which is how buffered events used to reach a half-initialised
  session).
- Backoff: `BACKOFF_BASE_MS` 300 doubling to `BACKOFF_MAX_MS` 10 000, full jitter in
  the upper half of the window; `attempt` resets on `session.ready`.
- Heartbeat: `hb` is answered with `hb.ack` before the fan-out. _Any_ inbound frame
  re-arms the watchdog, whose window is `heartbeatIntervalMs × 2.5` floored at 10 s
  (20 s default until `session.ready` names one). On expiry the socket is `drop()`ped
  as if the network died, so the reconnect policy applies.
- `TERMINAL_CLOSE_CODES` = 4400, 4401, 4403, 4404, 4409, 4413. 4429
  `BACKLOG_EXCEEDED` is deliberately _not_ terminal: the reconnect and its resume are
  the recovery. Names live in `libs/react-client/src/protocol/index.ts` (`CloseCode`),
  mirroring `chainlit.protocol.codec.CloseCode`.
- `LIVE_ONLY` = `hb.ack`, `session.clear`: never buffered. Everything else queues in
  `sendBuffer` and flushes in send order; `flush()` peeks-writes-shifts so a throwing
  write keeps the message at the head.

**`libs/react-client/src/transport.ts` — `ChatTransport`** is the single owner: one
socket and one `sendBuffer` per `ChainlitAPI`.

- `SessionDescriptor` = `{ sessionId, threadId?, chatProfile? }`. Identity is
  `(sessionId, threadId)` only (`sameSession`). `chatProfile` rides along as _payload_
  — the server names a profile on `session.ready` and `thread.resume`, and treating
  that as identity would tear the socket down to tell the server what it just said.
- `attach(descriptor, payload)` is idempotent: re-attaching the same identity only
  refreshes the hello payload, unless the phase is `closed` (then it is the retry that
  revives a given-up connection after a re-login).
- A `generation` counter is bumped by every `attach` and `detach`. `attach` awaits
  `client.stickyCookie(sessionId)` (`POST /set-session-cookie`) before opening;
  `openWhenPinned` re-compares generations afterwards and, if a newer intent overtook
  it, does nothing at all — neither opens nor closes.
- `detach()` bumps the generation, drops the socket, publishes `idle`. **The buffer is
  kept, but `send()` is `this.socket?.send(...)` — with no socket the message is
  silently dropped, not queued.**
- `TransportPhase`: `idle | connecting | ready | reconnecting | closed | superseded`.
  `superseded` is close 4409, sticky, `error: false`; `publish()` clears it unless the
  next state restates it, so any fresh attach resets it.
- `subscribe` / `getSnapshot` back `useSyncExternalStore` in `useChatData`.
  `onMessage(listener)` is an additive fan-out kept across socket rebuilds;
  `setSink(sink)` is a **single slot** — `step.stream.token` appends, so a handler
  table registered twice would double every streamed token. Frames reach the sink
  first, listeners second.
- `pageHasEstablishedConnection` is a module flag set on the first `session.ready`;
  `hello.pageLoad` is its negation. It tells the server "fresh page load, restore a
  pending ask's transcript, actions and element in full" versus "a reconnect of a page
  whose UI state is intact". `resetPageConnectionFlag()` exists for the copilot, which
  remounts an empty widget (`libs/copilot/index.tsx`).
- `installCypressHandle()` exposes `window.__chainlitSocket`, shaped like the old
  socket.io object (`connected`, `sendBuffer`, `connect`, `close`, `io.reconnection`,
  `io.engine.close`) — only when `window.Cypress` is set, never for
  `client.type === 'copilot'`.
- `chatTransportFor(client)` is a registry keyed on `` `${client.type} ${client.httpEndpoint}` ``,
  not on object identity, because the copilot constructs a new `ChainlitAPI` in its
  render body.

## 3. State (`libs/react-client/src/state.ts`)

`sessionDescriptorState` is one atom holding the whole descriptor, with
`sessionIdState`, `chatProfileState` and `threadIdToResumeState` as read/write
selectors over it. One atom rather than three because navigation moves all of them
together: as three atoms, every change reached the connect effect separately and the
intermediate combinations — a new session id still pointing at the previous thread —
were real states a socket got opened on. Resetting `sessionIdState` mints a fresh
uuid rather than writing a `DefaultValue`.

The descriptor atom carries `sessionStorageSessionIdEffect`: the id is persisted per
tab and reused **only** when Navigation Timing reports `type === 'reload'`. Any other
navigation — `target=_blank` tabs, which inherit a copy of sessionStorage, and
Chromium's `back_forward` for duplicated tabs — gets a fresh uuid, otherwise the new
tab would hijack the original's server session. `sessionIdStorage.key` is mutable so
the copilot can scope it (`chainlit-copilot-session-id:<server>`, set in
`libs/copilot/src/appWrapper.tsx` before `RecoilRoot` mounts).

Other atoms: `messagesState`, `elementState`, `tasklistState`, `actionState`,
`askUserState`, `loadingState`, `sideViewState`, `firstUserInteraction`,
`protocolErrorState` (the single error channel, filtered by `ErrorCode`),
`currentThreadIdState` (the thread the session is actually _in_, as opposed to the one
it was opened to resume), `threadHistoryState`, `configState`, `authState`, `userState`.

Two rules hold this together. **The transport is not in an atom**
(`libs/react-client/src/context.ts`): it is a live object with a socket in it, and
putting it in the store is what made `close()` write an atom from inside an atom
updater. **No atom update inside a state updater** — a component that needs another
atom's value at a moment in time reads it _through_ a setter
(`setMessages(previous => { kept = previous; return previous; })` in
`ChatProfileSwitchListener.tsx` and `useOpenThread.ts`) or through `useRecoilCallback`

- `snapshot` (`pruneStaleAskActions`, `endAsk`).

## 4. Hooks

### `useChatSession`

Owns the exhaustive frame table. `ServerMsgHandlers` is a mapped type over
`ServerMsg['t']` (`libs/react-client/src/protocol/messages.ts`), so a new tag on the
wire is a compile error rather than silence at runtime. All 23 server tags:

| Tag                        | Effect on state                                                                                                                                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `session.ready`            | clears the one-shot auth-failure guard; adopts `msg.chatProfile`                                                                                                                                             |
| `error`                    | writes `protocolErrorState`, warns                                                                                                                                                                           |
| `hb`                       | nothing — the socket already answered `hb.ack`                                                                                                                                                               |
| `reload`                   | sends `session.clear`, removes the persisted id, `location.reload()`                                                                                                                                         |
| `step.upsert`              | `addMessage` with the profile stamp; restates `wait` explicitly                                                                                                                                              |
| `step.update`              | `updateMessageById` with a patch (absent field = no opinion)                                                                                                                                                 |
| `step.delete`              | `deleteMessageById`                                                                                                                                                                                          |
| `step.stream.start`        | as `step.upsert`; clears a stored `wait`                                                                                                                                                                     |
| `step.stream.token`        | `updateMessageContentById` (appends)                                                                                                                                                                         |
| `element.upsert`           | fills `url` from `getElementUrl` when only `chainlitKey` is set; tasklists to `tasklistState`, the rest to `elementState`, by id                                                                             |
| `element.remove`           | drops the id from both                                                                                                                                                                                       |
| `action.add`               | upserts by id (a reconnect re-emit must not duplicate)                                                                                                                                                       |
| `action.remove`            | filters `actionState`                                                                                                                                                                                        |
| `ask.start`                | prunes a foreign ask's orphaned buttons, sets `askUserState` with a `reply` callback sending `ask.reply`, upserts the step, `loading = false`                                                                |
| `ask.end`                  | ends the ask **only if** it names the current ask's `stepId`; `timeout` also clears loading                                                                                                                  |
| `task.indicator`           | `loading = running`                                                                                                                                                                                          |
| `thread.resume`            | rebuilds messages/elements/tasklists, adopts `metadata.chat_profile`, sets `currentThreadId`, redirects to `/thread/<real id>` if the server resumed a different thread (all skipped for `viewer_read_only`) |
| `thread.first_interaction` | sets `firstUserInteraction` and `currentThreadId`                                                                                                                                                            |
| `thread.parent`            | no-op here; `ThreadReturnListener` subscribes                                                                                                                                                                |
| `thread.open`              | no-op here; `ThreadReturnListener` subscribes                                                                                                                                                                |
| `session.handoff`          | no-op here; `ChatProfileSwitchListener` owns it                                                                                                                                                              |
| `sidebar.set`              | merges into `sideViewState`: absent = leave alone, explicit null clears, empty `elements` closes it, and a sidebar open under the same `key` keeps its element array so a custom element is not remounted    |
| `toast`                    | sonner, by `type`                                                                                                                                                                                            |

The six client tags are `hello`, `hb.ack`, `session.clear`, `stop`, `message.send`,
`ask.reply`.

`sink.onClose` handles one case: a terminal 4403 `SESSION_FORBIDDEN` resets the
session id **once** (`authFailureHandledRef`), because a persisted id can belong to a
user who has since been replaced in this tab; a second refusal must surface as an
error rather than loop.

`attach(target, { userEnv })` installs the sink and calls `transport.attach`, composing
the hello payload as: `threadId: currentThreadId || target.threadId` (a session that
has moved on offers the thread it is _in_, not the one it was opened to resume),
`chatProfile: target.chatProfile`, `userEnv`.

### The rest

- **`useChatInteract`** — `clear(next)` sends `session.clear`, `detach()`es immediately
  (so late frames cannot land in a wiped chat), then mints the successor descriptor in
  **one** write: `sessionId: next.sessionId ?? uuidv4()`,
  `chatProfile: next.chatProfile ?? old.chatProfile`, `threadId: next.threadId`; and
  wipes messages, elements, tasklists, actions, sideview, ask, first interaction,
  protocol error and current thread id. `sendMessage` stamps id/`createdAt`, adds the
  step locally and sends `message.send`. `replyMessage` is a no-op while
  `askUser.awaitingReply`. `stopTask` clears streaming flags and sends `stop`.
- **`useChatData`** — atoms plus `useSyncExternalStore(transport.subscribe,
transport.getSnapshot)`. `disabled` covers not-connected, loading, file/action/element
  asks and `awaitingReply`; `superseded` is returned separately from `error`.
- **`useChatMessages`** — `messages`, `firstInteraction`, `threadId`
  (= `currentThreadIdState`).
- **`useConfig`** — SWR on `/project/settings?language=…&chat_profile=…`. The key
  includes the profile, and the config on screen is **not** blanked while the new one
  loads; blanking used to unmount everything gated on it, the thread page's resume
  included, which then remounted and resumed the thread a second time on a second
  session.

## 5. Frontend app flow

`frontend/src/main.tsx` wraps everything in `ChainlitContext.Provider` + `RecoilRoot`.

`App.tsx` holds the entire connection policy. `chatProfileOk` is true when the config
has loaded and either declares no profiles or one is chosen — the server reads the
profile out of `hello` and a session is born with it, so the first handshake must not
go out early. One effect: `if (isAuthenticated && isReady && chatProfileOk)
attach(descriptor, { userEnv })`. Because attach is idempotent, it states an intent
rather than performing a transition; there is no debounce and no guard. A second
effect toasts once on `superseded`; a third picks the default profile.

`router.tsx` routes `/`, `/env`, `/thread/:id?`, `/element/:id`, `/login`,
`/login/callback`, `/share/:id`, `*` → `/`.

`pages/Thread.tsx` mounts `AutoResumeThread` when the config is resumable, the route
is not `/share/`, and the URL thread is not already current. `AutoResumeThread.tsx`
issues the resume with `clear({ threadId: id })` — one write for "new session, and it
resumes this thread". Its guard is the descriptor itself (`if (idToResume === id)
return`), which is what makes it safe for the effect to re-run when the profile-keyed
config refetches after the resume names the thread's profile. A transport `error`
toasts and goes home; a `protocolError.code === THREAD_NOT_FOUND` does the same and
clears the error. Both are gated on `id === idToResume`, because on the commit that
mounts the component the resume has been issued but not rendered, and a leftover error
from the previous session would be read as this resume's answer. Both call `clear()`
on the way out, releasing the thread — otherwise picking the same thread again would
find the guard already satisfied and do nothing.

`ChatProfileSwitchListener.tsx` and `ThreadReturnListener.tsx` subscribe via
`transport.onMessage`, so they outlive every socket the transport builds and never
re-register. The switch listener acts on `session.handoff`: it validates the profile
name against the config, ignores a no-op (same profile, no kept transcript, no parked
transit message), then runs the whole teardown inside `flushSync` — a socket callback
schedules Recoil writes at sync priority while the router update is not in that lane,
and the split commit would render still located on `/thread/<old>` with the thread id
cleared, making `Thread` mount `AutoResumeThread` and resume the old thread over the
new chat. `clear({ sessionId: nextSessionId || undefined, chatProfile: name })` adopts
the id the backend parked the hand-off record under. The return listener runs
`thread.open` through `useOpenThread`, tracks the parent thread from `thread.parent`
(scoped to the session id) and `thread.resume`, drains the composer's parked
`openThreadRequestState`, and retires an in-flight transition via
`shouldRetireTransition` (`frontend/src/lib/openThread.ts`) on success, resume error,
session error/supersede, or navigation away.

`useOpenThread` probes `/project/thread/:id` with a raw `fetch` (the shared client
would send a 401 to the global login redirect), then does teardown, kept transcript
and `navigate('/thread/:id')` in one `flushSync`, after which the ordinary
`AutoResumeThread` path takes over. `NewChat.tsx` resets kept transcripts, calls
`clear()` and navigates home. `LeftSidebar/ThreadHistory.tsx` pages threads and
navigates to `/thread/:id`; it refreshes on `firstInteraction` and reorders on a new
user message.

## 6. Sequences

- **First load.** Descriptor mints (or, on F5, restores) a session id → config and auth
  load → default profile chosen → `attach` → `stickyCookie` → `hello` with
  `pageLoad: true` → `session.ready` → buffer flushes, phase `ready`.
- **New chat.** `clear()` → `session.clear` sent, `detach()`, one descriptor write with
  a fresh uuid → the `App` effect fires on the new descriptor → new socket.
- **Open a thread from history.** Navigate `/thread/:id` → `Thread` mounts
  `AutoResumeThread` → `clear({ threadId: id })` → attach with `hello.threadId = id` →
  `thread.resume` rebuilds the transcript and sets `currentThreadId` → `Thread` swaps
  the loader for `Chat`.
- **Profile switch.** `chatProfile` alone is payload, so a manual selection goes through
  `clear({ chatProfile })`, which changes the session id and therefore the identity.
- **Transport blip.** Socket drops → phase `reconnecting`, `error: false` → backoff
  reconnects → `hello` rebuilt from the _current_ payload with `pageLoad: false` →
  buffer flushes on `session.ready`. Nothing in the app reconnects by hand.
- **Second-tab takeover.** The new tab's `hello` supersedes the session; this tab
  closes 4409 → phase `superseded` (sticky, not an error) → composer disabled, one
  toast. Only a different descriptor revives it.
- **Server hand-off.** `session.handoff` → validated → `flushSync` teardown with the
  kept transcript and its boundary → `clear` with the server's `nextSessionId` →
  attach → the new session picks up the parked transit message. `thread.open` →
  availability probe → excursion kept → navigate → the ordinary resume path.

## 7. Testing

`libs/react-client/tests/chatTransport.spec.ts` stubs `WebSocket` with a
test-driven `FakeWebSocket` via `vi.stubGlobal` and fake timers. Its tests guard, one
each: one socket per descriptor however often attached; exactly one rebuild for a new
descriptor, old one closed; a rebuild for the same session on a new thread; an attach
overtaken during the sticky-cookie call neither opening nor closing; a `detach` during
that call cancelling outright; queued work surviving a rebuild and flushing on
`session.ready`; `superseded` staying sticky through a re-attach; a `closed` transport
reopening on a fresh attach; a blip healing without a new attach; a new `chatProfile`
reaching the next handshake without a reconnect; the payload `threadId` beating the
descriptor's; sink-before-listeners ordering; listeners surviving a rebuild; and
`onClose` reaching the sink so a refused session id can be replaced.

`frontend/tests/` runs under `frontend/vitest.config.ts` (jsdom,
`tests/setup-tests.ts`) and, because its `include` is `./**`, also covers
`chainlitSocket.spec.ts` — which imports `libs/react-client/src/socket.ts` by relative
path and guards hello-before-anything, buffer ordering, a reply kept across a
reconnect, a send from a `session.ready` listener staying behind the buffered ones,
the never-buffered `hb.ack`, backoff stopping on a terminal code, a refused upgrade
reported as never-opened, no reconnect after a deliberate close, both watchdog cases,
fan-out order, and `websocketUrl`. Other specs cover message-tree merging, ask-action
pruning, transcript freezing, wait messages, compact steps, icons, content rendering,
`NewChat` and `openThread`. The three `displayModePrecedence.spec.ts` failures
(copilot display-mode resolution) are pre-existing, not a regression.

E2E is Cypress: 48 spec directories under `cypress/e2e/`, each with its own `main.py`
and `.chainlit/`. `cypress.config.ts` starts a real backend per spec
(`cypress/support/run.ts` spawns `uv run chainlit run … -h --ci` in its own process
group and waits for "Your app is available at"), kills whatever holds port 8000 before
and after, and never matches processes by name so a developer's own server is safe.
`cypress/e2e/ask_reconnect/spec.cy.ts` is the one spec driving
`window.__chainlitSocket`: it disables reconnection, closes the socket, asserts the
click landed in `sendBuffer`, reconnects, and separately uses `io.engine.close()` to
simulate a dead network. On a ru-RU machine three specs are known-red because the fork
ships `backend/chainlit/translations/ru.json` while specs assert English strings
(`custom_theme` is one); the fix pattern is stubbing `navigator.language` in
`cy.visit(..., { onBeforeLoad })`, as `cypress/e2e/oauth_auth/spec.cy.ts` does.

Gates before committing: `pnpm type-check`, `pnpm lint`, `pnpm format-check` (all take
paths; `format` and `lint:fix` write).

## 8. Traps

- **Never close a socket from inside a Recoil updater.** That is what putting the
  transport in an atom caused, and why `useChatTransport` reads it from a module
  registry (`libs/react-client/src/context.ts`).
- **Never make `chatProfile` part of the descriptor identity.** The server announces a
  profile on `session.ready` and `thread.resume`; as identity, every handshake would
  rebuild the socket to tell the server what it just told us — a reconnect storm with
  a replayed resume each time.
- **Do not read `protocolError` before the resume you are judging.** `AutoResumeThread`
  gates every failure branch on `id === idToResume`; without it, an error left over
  from the session `clear()` is about to drop is read as this resume's answer.
- **`transport.send()` after `detach()` is dropped, not buffered** — `send` is
  `this.socket?.send(...)`, and the buffer only fills through a live socket. This is
  why `ask.reply` is a plain message and not an ack.
- **The copilot builds a new `ChainlitAPI` on every render** of `AppWrapper`
  (`makeApiClient` in the render body); `chatTransportFor` is keyed on
  `type + httpEndpoint` for that reason.
- **One sink, many listeners.** A second handler table would double every
  `step.stream.token`; components needing two or three tags use `transport.onMessage`.
- **`sessionStorage`, not `localStorage`, and only on a true reload** — anything else
  lets a duplicated tab hijack a live server session.
- **Build order.** A stale `libs/react-client/dist` silently ships old client code into
  `frontend/dist`, and from there into the wheel.
