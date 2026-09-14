# Client architecture

The browser side of this fork: the publishable `@chainlit/react-client` library and
the `frontend` single-page app. socket.io is
gone; everything below speaks the msgspec-tagged JSON protocol described in
`backend/chainlit/protocol/README.md` over one native websocket.

Every claim here was read out of the source at the path named next to it.

## 1. Packages and build pipeline

`pnpm-workspace.yaml` lists two packages:

| Package                  | Path                 | Output                                 | Type-check     |
| ------------------------ | -------------------- | -------------------------------------- | -------------- |
| `@chainlit/react-client` | `libs/react-client/` | `dist/` via tsup (esm + cjs + `.d.ts`) | `tsc --noemit` |
| `@chainlit/app`          | `frontend/`          | `frontend/dist/` via Vite              | `tsc --noemit` |

A third package, the embeddable `@chainlit/copilot` widget, was removed on
14.09.2026: the fork's one consumer never embedded it, and it was the only package
whose `tsc` could not be made to pass.

`react-client` must be built before `frontend`, and it is: `frontend/package.json`
declares `"@chainlit/react-client": "workspace:^"`, so the root
`pnpm build` (`pnpm run --recursive build`) orders the packages topologically.
Never run `cd frontend && pnpm build` against a stale
`libs/react-client/dist` — the app imports the built package, not the sources.

`uv build` in `backend/` runs the hatchling hook in `backend/build.py`:
`pnpm install --frozen-lockfile`, `pnpm build` at the repo root, then
`frontend/dist` → `backend/chainlit/frontend/dist`. It is `exclude`d from the source
tree but listed
as an `artifact` in `backend/pyproject.toml`, which is how built assets reach the wheel
uncommitted. The hook returns early when `../package.json` is absent — a wheel built
from an sdist that already carries the assets.

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
- `TERMINAL_CLOSE_CODES` = 4400, 4401, 4409, 4413. 4429 `BACKLOG_EXCEEDED` is
  deliberately _not_ terminal: the reconnect and its resume are the recovery. 4403
  `SESSION_FORBIDDEN` and 4404 `THREAD_FORBIDDEN` are gone from both sides — nothing
  can refuse a name the client chose, because the client names no session and a thread
  it may not have is answered with a fresh one. Names live in
  `libs/react-client/src/protocol/index.ts` (`CloseCode`), mirroring
  `chainlit.protocol.codec.CloseCode`.
- `LIVE_ONLY` = `hb.ack`, `session.clear`: never buffered. Everything else queues in
  `sendBuffer` and flushes in send order; `flush()` peeks-writes-shifts so a throwing
  write keeps the message at the head.

**`libs/react-client/src/transport.ts` — `ChatTransport`** is the single owner: one
socket and one `sendBuffer` per `ChainlitAPI`.

- `SessionDescriptor` = `{ threadId?, chatProfile? }`. Identity is the **thread**
  and nothing else (`sameSession`): the thread is the only name this side of the wire
  has for a conversation, and `hello` carries no session id at all. `chatProfile` rides
  along as _payload_ — the server names a profile on `session.ready` and
  `thread.resume`, and treating that as identity would tear the socket down to tell the
  server what it just said. Two descriptors with no thread compare equal, which is
  safe only because `clear()` `detach()`es first and `detach` forgets the descriptor;
  that is what makes two New Chats in a row two connections, and it is pinned as such.
- `attach(descriptor, payload)` is idempotent: re-attaching the same identity only
  refreshes the hello payload, unless the phase is `closed` (then it is the retry that
  revives a given-up connection after a re-login). It opens **synchronously** — there
  is nothing to await first. `POST /set-session-cookie` used to be awaited here to pin
  a multi-worker deployment to the worker that would own the session; with the id
  minted by the server there is nothing to pin with before the handshake, so the
  endpoint and `api.stickyCookie` are deleted and affinity is the balancer's problem.
- A `generation` counter is bumped by every `attach` and `detach` and captured by the
  socket built under it: a replaced socket still owns timers and a pending close, and
  the fence is what stops its `onStatus`/`onClose` publishing a phase over its
  successor.
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
  `hello.pageLoad` is its negation. It tells the server "fresh page load, restore the
  transcript, elements, actions and any pending ask in full" versus "a reconnect of a
  page whose UI state is intact". There is no `resetPageConnectionFlag()`: it existed
  for an embedder that remounted an empty widget in a live page, and that embedder is
  gone.
- `installCypressHandle()` exposes `window.__chainlitSocket` (`connected`,
  `sendBuffer`, `connect`, `close`, `io.reconnection`, `io.engine.close`) only when
  `window.Cypress` is set. It exposes the connection and never the session id: the id
  is the server's handle on this user's live objects, and a page script holding it
  could act as them.
- `chatTransportFor(client)` is a registry keyed on `client.httpEndpoint`, not on
  object identity, so a host that builds its `ChainlitAPI` in a render body is not
  handed a second socket per render.

## 3. State (`libs/react-client/src/state.ts`)

`sessionDescriptorState` is one atom holding the whole descriptor, with
`chatProfileState` and `threadIdToResumeState` as read/write selectors over it. One
atom rather than two because navigation moves both together: as separate atoms every
change reached the connect effect on its own, and the intermediate combinations were
real states a socket got opened on.

**Nothing about a conversation is written down between page loads.** The descriptor's
only effect reads `sessionDescriptorSeed.threadId` — a function the host sets before
`RecoilRoot` mounts; `frontend/src/main.tsx` points it at `/thread/:id` in
`window.location` (`frontend/src/lib/threadAddress.ts`), so the URL is the only place
a request for a thread can come from, and it is answered synchronously, before
`ThreadAddressSync` compares it on mount. There is no `sessionIdStorage`, no
Navigation-Timing reload branch and no storage effect: the session id is minted by the
server, so there is nothing per-tab to persist and nothing a duplicated tab could copy
in order to claim a live session. A second tab on the same address is a **takeover**
the server decides on (close 4409 to the first), which is the owner's rule, not a
collision this side prevents.

`sessionIdState` is a plain atom, `string | undefined`, written by the `session.ready`
handler from `msg.sessionId` and reset by `clear()`. It is an answer, not a request:
nothing connects on it, and it is only the handle uploads, action calls, feedback and
custom-element writes address the session with over HTTP. Every one of those callers
has to tolerate `undefined` — before the first `session.ready`, and from the moment
`clear()` gives the session up. `uploadFile` throws, `ActionButton` and the two
feedback handlers return early; the composer was already gated on `connected`.

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

| Tag                        | Effect on state                                                                                                                                                                                                                               |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `session.ready`            | writes `msg.sessionId` to `sessionIdState` **and to a ref first**; adopts `msg.chatProfile`; writes `msg.threadId` to `currentThreadIdState` — the server's answer to whatever the URL asked                                                  |
| `error`                    | writes `protocolErrorState`, warns                                                                                                                                                                                                            |
| `hb`                       | nothing — the socket already answered `hb.ack`                                                                                                                                                                                                |
| `reload`                   | sends `session.clear` — the server's cue to tear the session down and give the thread up — then `location.reload()`                                                                                                                           |
| `step.upsert`              | `addMessage` with the profile stamp; restates `wait` explicitly                                                                                                                                                                               |
| `step.update`              | `updateMessageById` with a patch (absent field = no opinion)                                                                                                                                                                                  |
| `step.delete`              | `deleteMessageById`                                                                                                                                                                                                                           |
| `step.stream.start`        | as `step.upsert`; clears a stored `wait`                                                                                                                                                                                                      |
| `step.stream.token`        | `updateMessageContentById` (appends)                                                                                                                                                                                                          |
| `element.upsert`           | fills `url` from `getElementUrl` when only `chainlitKey` is set; tasklists to `tasklistState`, the rest to `elementState`, by id                                                                                                              |
| `element.remove`           | drops the id from both                                                                                                                                                                                                                        |
| `action.add`               | upserts by id (a reconnect re-emit must not duplicate)                                                                                                                                                                                        |
| `action.remove`            | filters `actionState`                                                                                                                                                                                                                         |
| `ask.start`                | prunes a foreign ask's orphaned buttons, sets `askUserState` with a `reply` callback sending `ask.reply`, upserts the step, `loading = false`                                                                                                 |
| `ask.end`                  | ends the ask **only if** it names the current ask's `stepId`; `timeout` also clears loading                                                                                                                                                   |
| `task.indicator`           | `loading = running`                                                                                                                                                                                                                           |
| `thread.resume`            | rebuilds messages/elements/tasklists, adopts `metadata.chat_profile`, sets `currentThreadId` (all skipped for `viewer_read_only`). No redirect: a thread other than the one asked for is named in `session.ready`, and the address follows it |
| `thread.first_interaction` | sets `firstUserInteraction` and `currentThreadId`                                                                                                                                                                                             |
| `thread.parent`            | no-op here; `ThreadReturnListener` subscribes                                                                                                                                                                                                 |
| `thread.open`              | no-op here; `ThreadReturnListener` subscribes                                                                                                                                                                                                 |
| `session.handoff`          | no-op here; `ChatProfileSwitchListener` owns it (it carries `nextThreadId`, not a session id)                                                                                                                                                 |
| `sidebar.set`              | merges into `sideViewState`: absent = leave alone, explicit null clears, empty `elements` closes it, and a sidebar open under the same `key` keeps its element array so a custom element is not remounted                                     |
| `toast`                    | sonner, by `type`                                                                                                                                                                                                                             |

The six client tags are `hello`, `hb.ack`, `session.clear`, `stop`, `message.send`,
`ask.reply`.

`sink.onClose` does nothing. It used to reset the session id once on a terminal 4403
`SESSION_FORBIDDEN`, because a persisted id could name a session belonging to a user
who had since been replaced in this tab. Neither half survives: the client names no
session, so there is nothing to refuse, and 4403 is not a close code any more.

The session id reaches the element handlers through a **ref written inside the
`session.ready` handler**, not through the render-captured value. `element.upsert`
mints `?session_id=…` and can follow `session.ready` in the same tick; a handler table
rebuilt on the next render would still hold the previous session's id — or none at all
on the first connection. `session.ready` is the first frame of every socket, so the
ref is never stale and nothing has to clear it. `ThreadReturnListener` does the same
for `thread.parent`, which it scopes to the session id.

`attach(target, { userEnv })` installs the sink and calls `transport.attach`, composing
the hello payload as: `threadId: currentThreadId || target.threadId` (a session that
has moved on offers the thread it is _in_, not the one it was opened to resume),
`chatProfile: target.chatProfile`, `userEnv`.

### The rest

- **`useChatInteract`** — `clear(next)` sends `session.clear` (which now means "tear
  the session down and give the thread up", not "cancel its work"), `detach()`es
  immediately — so late frames cannot land in a wiped chat, and so the next `attach`
  has no descriptor to compare against — then mints the successor in **one** write:
  `chatProfile: next.chatProfile ?? old.chatProfile`, `threadId: next.threadId`; and
  wipes the session id, messages, elements, tasklists, actions, sideview, ask, first
  interaction, protocol error and current thread id. `sendMessage` stamps id/`createdAt`, adds the
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

`ThreadAddressSync.tsx` (mounted in `pages/Page.tsx`, so on every route) owns **both**
directions of "the URL asks and `session.ready` answers". They used to be two
components — `AutoResumeThread` under `/thread/:id` asked, a listener in `Page`
answered — and a commit in which the route had moved but the session had not read as a
request for the thread the session was already being given.

_Asking._ The route's thread comes from `threadIdFromLocation(pathname, '')`, not
`useParams`, so `/share/:id` and `/element/:id` yield nothing. When it names a thread
that is neither `currentThreadId` (the server's answer) nor `idToResume` (the
descriptor's request), it issues `clear({ threadId: id })` — one write for "new
session, and it resumes this thread". Both guards are load-bearing: the descriptor one
is what makes it safe for the effect to re-run when the profile-keyed config refetches
after the resume names the thread's profile, and the `currentThreadId` one is what
keeps the steady state from reading as a fresh request. `openThreadTransitionState`
distinguishes a return (transcript kept) from a plain open out of the history (blank).
A transport `error` toasts, `clear()`s — releasing the thread, or picking it again
would find the guard satisfied and do nothing — and goes home, gated on
`id === idToResume` **and** `id !== currentThreadId`: before the answer it is a refusal,
after it a dead connection, and bouncing the user out of the chat they are reading for
one would be absurd.

_Answering._ `session.ready` names the thread and the address follows with
`navigate(..., { replace: true })` — `navigate`, because react-router's history
subscribes to `popstate` only and a bare `history.replaceState` leaves `useParams()`
stale. This gives a chat begun at `/` its `/thread/<id>` address, and carries a resume
the server would not grant onto the thread it chose instead (no error frame). Only
when the config is `threadResumable`: an app without resume hooks starts over on a
reload, and `Thread.tsx` renders `/thread/<id>` read-only for it.

`ChatProfileSwitchListener.tsx` and `ThreadReturnListener.tsx` subscribe via
`transport.onMessage`, so they outlive every socket the transport builds and never
re-register. The switch listener acts on `session.handoff`: it validates the profile
name against the config, ignores a no-op (same profile, no kept transcript, no parked
transit message), then runs the whole teardown inside `flushSync`. **That `flushSync`
is still required** — more so now that `ThreadAddressSync` is mounted on every route: a
socket callback schedules Recoil writes at sync priority while the router update is
not in that lane, and the split commit would render still located on `/thread/<old>`
with the descriptor already asking for `<next>`, which `ThreadAddressSync` reads as a
request for `<old>` and answers by clearing the session the hand-off just created.
Inside it: `clear({ threadId: nextThreadId || undefined, chatProfile: name })` adopts
the thread the backend parked the hand-off record under — **descriptor first, then
navigation**, the invariant everywhere the two move together — followed by
`navigate('/thread/<next>')` (a push: a switch is a new conversation) and an
`openThreadTransitionState` entry, which is what keeps `Chat` mounted over a kept
transcript while the new address waits for its `session.ready` instead of showing
`Thread.tsx`'s loader. The address only moves for a `threadResumable` app, the same
rule the answering direction follows: in an app without resume hooks `/thread/<id>` is
`ReadOnlyThread`, whose fetch of a thread with no row yet 404s and sends the user home,
so a switch there would bounce out of the chat it had just made. Without a
`nextThreadId`, or without resume, it is an ordinary new chat at `/`. The return listener runs `thread.open` through
`useOpenThread`, tracks the parent thread from `thread.parent` (scoped to the session
id it takes off `session.ready`) and `thread.resume`, and retires an in-flight
transition via `shouldRetireTransition` (`frontend/src/lib/openThread.ts`) on success,
session error/supersede, or navigation away — which is also how a refused resume
retires it: the address has moved to the thread the server named instead.

`useOpenThread` probes `/project/thread/:id` with a raw `fetch` (the shared client
would send a 401 to the global login redirect), then does teardown, kept transcript
and `navigate('/thread/:id')` in one `flushSync`, after which `ThreadAddressSync` reads
the new address as the request and clears the session into it. The composer's
`OpenParentThreadButton` calls it directly; it used to park a request in an atom for
`ThreadReturnListener` to drain, because the composer also rendered in an embedder
with no router. The button is split in two so the hook (which reaches for the router
and the API client) is only mounted in a chat that actually has a parent. `NewChat.tsx` resets kept transcripts, calls
`clear()` and navigates home. `LeftSidebar/ThreadHistory.tsx` pages threads and
navigates to `/thread/:id`; it refreshes on `firstInteraction` and reorders on a new
user message.

## 6. Sequences

- **First load.** The descriptor takes the URL's thread from the seed (or none) →
  config and auth load → default profile chosen → `attach` → `hello` with
  `pageLoad: true` → `session.ready` names the session id and the thread → buffer
  flushes, phase `ready`.
- **Reload of a live thread.** The address still names the thread, so `hello` asks for
  it and the server hands the **same live session** back: the transcript is replayed
  from memory and `on_chat_start`/`on_chat_resume` do not run again. A thread whose
  session the reaper has collected resumes from the database instead.
- **New chat.** `clear()` → `session.clear` sent, `detach()` (which forgets the
  descriptor), one descriptor write → the `App` effect fires → new socket. Two in a row
  are two sockets for exactly that reason, not because anything is nonced.
- **Open a thread from history.** Navigate `/thread/:id` → `ThreadAddressSync` reads a
  route naming neither the current nor the requested thread → `clear({ threadId: id })`
  → attach with `hello.threadId = id` → `thread.resume` rebuilds the transcript and
  `session.ready` sets `currentThreadId` → `Thread` swaps the loader for `Chat`.
- **Profile switch.** `chatProfile` alone is payload, so a manual selection goes through
  `clear()`, which drops the thread and therefore the identity.
- **Transport blip.** Socket drops → phase `reconnecting`, `error: false` → backoff
  reconnects → `hello` rebuilt from the _current_ payload with `pageLoad: false` →
  buffer flushes on `session.ready`. Nothing in the app reconnects by hand.
- **Second-tab takeover.** A second tab on `/thread/<same>` asks for the same thread,
  and the thread is the identity: the server hands the session to the newcomer and
  closes this one 4409 → phase `superseded` (sticky, not an error) → composer disabled,
  one toast. Only a different descriptor revives it. A duplicated tab or a
  `target=_blank` is the same thing, deliberately — it is one conversation, open in one
  place.
- **Server hand-off.** `session.handoff` → validated → `flushSync` teardown with the
  kept transcript and its boundary → `clear` with the server's `nextThreadId` →
  `navigate('/thread/<next>')` if the app can resume, else `/` → attach → the new
  session picks up the parked transit message. `thread.open` → availability probe → excursion kept → navigate → the
  ordinary resume path.

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
`NewChat`, `openThread` and `threadAddressSync` (both directions of the
address↔session loop). `displayModePrecedence.spec.ts`, whose three failures were the
standing known-red, is deleted along with the embedder whose display mode it resolved.

E2E is Cypress: 48 spec directories under `cypress/e2e/`, each with its own `main.py`
and `.chainlit/`. `cypress.config.ts` starts a real backend per spec
(`cypress/support/run.ts` spawns `uv run chainlit run … -h --ci` in its own process
group and waits for "Your app is available at"), kills whatever holds port 8000 before
and after, and never matches processes by name so a developer's own server is safe.
`cypress/e2e/ask_reconnect/spec.cy.ts` is the one spec driving
`window.__chainlitSocket`: it disables reconnection, closes the socket, asserts the
click landed in `sendBuffer`, reconnects, and separately uses `io.engine.close()` to
simulate a dead network. Its reload cases, and `set_chat_profile`'s, state the new F5
rule: a live thread comes back as the same session, transcript and all, so only
client-side state (a kept transcript, its dividers) is dropped. `cypress/support`'s
`onServerFrame(tag, cb)` replaces `setupWebSocketListener`, which still parsed
Engine.IO `42` framing; `custom_element_auth` takes the session id off
`session.ready` now that `POST /set-session-cookie` is gone. On a ru-RU machine three specs are known-red because the fork
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
- **Do not read `protocolError` before the resume you are judging.** `ThreadAddressSync`
  gates every failure branch on `id === idToResume`; without it, an error left over
  from the session `clear()` is about to drop is read as this resume's answer. The
  second gate, `id !== currentThreadId`, is what stops a later dead connection being
  read as the same refusal.
- **`transport.send()` after `detach()` is dropped, not buffered** — `send` is
  `this.socket?.send(...)`, and the buffer only fills through a live socket. This is
  why `ask.reply` is a plain message and not an ack.
- **One sink, many listeners.** A second handler table would double every
  `step.stream.token`; components needing two or three tags use `transport.onMessage`.
- **A second tab on the same thread is a takeover, not a collision.** Nothing is
  stored per tab any more — there is no id for a duplicated tab to inherit and no
  reload branch to get right. The thread in the address bar is the whole identity, and
  a second window asking for it takes the session over (4409 to the first). That is the
  decision, not an accident to be defended against, and it is why an anonymous
  deployment's capability is now the URL rather than a per-tab secret.
- **Build order.** A stale `libs/react-client/dist` silently ships old client code into
  `frontend/dist`, and from there into the wheel.
