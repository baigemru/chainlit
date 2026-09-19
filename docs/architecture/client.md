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

### What a custom element may rely on

A mounted element keeps its React state. `CustomElement/` compiles the source
once per instance with `react-runner`'s `generateElement`, unwraps the result to
a component **type** (`compile.ts`) and renders that type inside the shared
`ErrorBoundary`; the scope the element closes over is one object built per
`element.id` and mutated in place afterwards. So a run starting or finishing, an
Ask opening or closing, an `element.update` from the server, and a parent that
hides and shows the element all leave it mounted. It is remounted by exactly two
things: a change of `element.id`, and a change of the source text.

The consequences for an element's author, benefits and costs together:

- `props` is **one object for the element's lifetime**, never replaced. Reading
  `props.foo` during render always sees the current value, and
  `updateElement(Object.assign(props, { … }))` still works.
- `updateElement` is what makes a change **survive**. It writes the element in
  two places at once (`PUT /project/element` → `controllers/project.update_element`):
  the row, which a cold resume reads, and the session's own copy of the element —
  the transcript entry it hangs off and any panel slot showing it — which is what a
  reload replays. A change kept only in `props` is gone on the next F5, and until
  the session copy existed a change written with `updateElement` came back stale
  anyway. The row is for **display**: an application that owns the fact the element
  is showing keeps it somewhere of its own, not in the element it drew it with.
- The `id` a panel element sees on the client is the **row id the engine minted**
  from the thread and the name passed to `set_slot`, not that name itself. An element
  that needs its name at runtime gets it through `props` (the consumer's `panelId`).
- `useState(props.foo)` is the standard React trap and now bites for real: an
  initialiser runs once, and since the element no longer remounts on every update
  the state will never catch up with the prop. Derive from `props` during render,
  or key off the value yourself.
- For the same reason `useEffect(…, [props])` fires **once and never again**, and
  a memoized child handed `props` directly never re-renders. The sync deep-copies
  the server's object, so nested values do get a new identity on each update:
  depend on `[props.items]`, not on `[props]`.
- The element file must export a **function**. `export default memo(Component)`
  renders nothing, and always has — `generateElement` accepts a function, an
  element or a string, and a `memo` object is none of those. A file that is a
  bare JSX expression is worse: react-runner wraps it into a default export, the
  values in it were evaluated once at compile time, and nothing can update it
  afterwards.
- The source is fetched once per element **name** per page (`source.ts`) and
  shared by every instance. A failed fetch is _not_ remembered (`get` rejects on
  any non-2xx, and a 401 mid-refresh must not pin the Alert), so the next mount
  of that name tries again. A _successful_ one is remembered for the life of the
  page: editing `public/elements/<name>.jsx` in a dev server needs a browser
  reload, because the backend's watcher only reloads on `.py`, `chainlit.md` and
  `config.toml` (`backend/chainlit/cli/__init__.py:96`).
- A throw inside an element's own render is contained: the boundary shows an
  Alert in place of that card and logs the throw, and the rest of the message
  list survives. It does not recover — the boundary is keyed on `element.id`, so
  a card that throws once is an Alert until the page is reloaded. HEAD latched in
  the same way, one level up: its `onRendered` wrote an error the fetch effect
  never cleared, replacing the whole card.

## 2. Transport

Two objects, in two files, with a clear split of duties.

**`libs/react-client/src/socket.ts` — `ChainlitSocket`** is one reconnecting
websocket.

- `websocketUrl(httpEndpoint)` derives `ws(s)://host/<root>/ws`; `WEBSOCKET_PATH` is
  `/ws`. Auth is cookie-only — nothing in the URL or the handshake frame — and a
  refusal of those cookies arrives as close **4401 on an accepted socket**: the auth
  middleware accepts the upgrade itself before it closes, because a refusal sent
  before the accept reaches the browser as an HTTP 403 whose status the WebSocket API
  never exposes. `opened: false` therefore means one thing now — nothing completed the
  upgrade (an unreachable server, or a proxy refusing it) — and that is exactly the
  case the backoff is right for.
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
`askUserState`, `loadingState`, `elementSidebarState`, `firstUserInteraction`,
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

| Tag                        | Effect on state                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `session.ready`            | writes `msg.sessionId` to `sessionIdState` **and to a ref first**; adopts `msg.chatProfile`; writes `msg.threadId` to `currentThreadIdState` — the server's answer to whatever the URL asked                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `error`                    | writes `protocolErrorState`, warns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `hb`                       | nothing — the socket already answered `hb.ack`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `reload`                   | sends `session.clear` — the server's cue to tear the session down and give the thread up — then `location.reload()`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `step.upsert`              | `addMessage` with the profile stamp; restates `wait` explicitly                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `step.update`              | `updateMessageById` with a patch (absent field = no opinion)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `step.delete`              | `deleteMessageById`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `step.stream.start`        | as `step.upsert`; clears a stored `wait`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `step.stream.token`        | `updateMessageContentById` (appends)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `element.upsert`           | fills `url` from `getElementUrl` when only `chainlitKey` is set; tasklists to `tasklistState`, the rest to `elementState`, by id                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `element.remove`           | drops the id from both                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `action.add`               | upserts by id (a reconnect re-emit must not duplicate)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `action.remove`            | filters `actionState`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `ask.start`                | prunes a foreign ask's orphaned buttons, sets `askUserState` with a `reply` callback sending `ask.reply`, upserts the step, `loading = false`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `ask.end`                  | ends the ask **only if** it names the current ask's `stepId`; `timeout` also clears loading                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `task.indicator`           | `loading = running`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `thread.resume`            | rebuilds messages/elements/tasklists, adopts `metadata.chat_profile`, sets `currentThreadId` (all skipped for `viewer_read_only`). No redirect: a thread other than the one asked for is named in `session.ready`, and the address follows it                                                                                                                                                                                                                                                                                                                                                                                   |
| `thread.first_interaction` | sets `firstUserInteraction` and `currentThreadId`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `thread.parent`            | no-op here; `ThreadReturnListener` subscribes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `thread.open`              | no-op here; `ThreadReturnListener` subscribes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `session.handoff`          | no-op here; `ChatProfileSwitchListener` owns it (it carries `nextThreadId`, not a session id)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `sidebar.state`            | writes `elementSidebarState` **whole**: the panel is state the server owns, and this frame is its projection. Slots name their elements by id, resolved against `elementState` (the `element.upsert`s precede it on the one FIFO queue; a missing id is a server bug — skipped with a `console.warn`). `rev` is stored and quoted back in every `sidebar.user`. One branch on the screen lives here: the first frame after a `pageLoad` connection on a viewport narrower than `MOBILE_BREAKPOINT` is applied hidden and answered with `sidebar.user hide`, so a reload on a phone does not drop a 95%-wide sheet over the chat |
| `toast`                    | sonner, by `type`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

The seven client tags are `hello`, `hb.ack`, `session.clear`, `stop`, `message.send`,
`ask.reply` and `sidebar.user`.

**The element panel** (`useElementSidebar`, `components/ElementSideView.tsx`). The atom
is `{slots, active, visible}` and never `undefined`: hiding the panel is not closing it,
and closing one tab is not closing the rest. `dispatch(op)` applies the operation
locally — always, so the UI answers the click on the same frame — and sends
`sidebar.user` quoting the `rev` it was last shown; the server answers structural
operations and refusals with the whole state, which overwrites the guess, and says
nothing to `hide`/`show`/`activate` so a fast hide-then-show does not draw the middle
— **while the `rev` still matches**. A scalar made against a state a frame in flight
has already replaced is answered too, because nothing else ever would, and the answer
lands after that frame. The `rev` is the server's number: a local dispatch never moves
it. `dispatch(op, {local: true})` skips the
send, which is what `ReadOnlyThread` uses: its elements come from the REST record of a
thread the live session has never been in, so the server would refuse the preview and
the refusal would wipe it. The view is Radix `Tabs` with **every** `TabsContent`
`forceMount`ed and `hidden` when inactive — a conditional render would remount a custom
element on every tab switch, which is what the retired `key` field was really about.
The tab strip appears whenever there is more than one slot, on both layouts and
whatever the active slot is (`canvas` gives up the header of a _single-slot_ panel,
never the way out of itself); the "×" that closes a slot is a sibling of its
`TabsTrigger`, never a child, and a single slot carries its own beside the title —
distinct from the back arrow and the sheet's close, which hide.

`sink.onClose` has one branch: **close 4401 clears `userState`**, which is the answer
`useApi` already gives an HTTP 401 (`api.ts:63-67`). The socket learns the same thing
over its own wire, and `useAuth`'s `isAuthenticated: !!user` turns it into the
redirect — `AppWrapper` sends the tab to `/login`, and `App.tsx`'s attach effect, gated
on the same flag, stops re-attaching. Without it a cookie that expired under an open
page left the tab on "reconnecting" forever: 4401 is terminal, so the socket stopped,
but nothing told the app, and the next run of the attach effect would have re-attached
the closed transport and earned another 4401. The setter comes from
`useSetRecoilState(userState)` rather than `useAuthState()` — the same write, without
subscribing all ten callers of the hook to `userState` and `authState`. A 1006 leaves
the user alone: that is a blip the transport heals. (The branch that used to live here
reset the session id on a terminal 4403; neither half survives — the client names no
session, so there is nothing to refuse, and 4403 is not a close code any more.)

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
  wipes the session id, messages, elements, tasklists, actions, the element panel, ask, first
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
`/login/callback`, `/share/:id`, `/account`, `*` → `/`.

**The account page.** `pages/Account.tsx` fetches `GET /project/account` with `useApi`
and gets back four things: the JSON Schema `msgspec.json.schema` emitted for the
`msgspec.Struct` the application registered with `@cl.account`, the current values, a
`readonly` flag and an optional message. A 404 is a configuration fact rather than a
breakage — the application declared no account page — so it renders as an info notice
and not as an error. The page itself knows nothing about the fields: it hands schema and
values to `components/SchemaForm`, which resolves the schema into controls (a top-level
nested Struct becomes a tab, a deeper one a fieldset; `boolean` a switch, an `enum` a
select, a number an input or — with `x-widget: slider` — a range, `date`/`date-time` the
native pickers, `list[str]` a tag input, `list[Literal]` a checkbox list) and reads the
two `x-` extensions this fork puts in `Meta(extra_json_schema=…)`: `x-widget` picks the
control (`slider`, `textarea`, `password`, `radio`, `markdown`, `link`) and
`x-enum-labels` names the options. A control the resolver does not recognise renders
read-only rather than disappearing — the form has to keep submitting a field it cannot
draw. Saving `PUT`s the values object back and re-renders from the response, which is
what the engine stored; a rejection carries the server's `detail`, and the msgspec path
in it (`at $.calculation.margin`) is the only field addressing the form has. That save
goes out through a copy of the context client with `onError` cleared, the way `useApi`
silences it for reads, so one failure raises one toast.

An outbound link belongs on that page as a field, not as a menu row: «Платёжный кабинет»
→ the application's own `/billing/portal` is a `readOnly` string with
`x-widget: "link"`, which `SchemaForm` draws as a button-styled anchor whose `href` is
the value. The row that opens the page lives in `components/header/UserNav.tsx` and
appears only when the config carries `ui.account.enabled`; its text is
`ui.account.title` when the application set one, else the `navigation.user.menu.account`
translation. That is the whole user menu now — name, account, logout — in both the
avatar dropdown and the phone overflow rendering, which are the same `items` fragment.

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
and the API client) is only mounted in a chat that actually has a parent. Whether it is
drawn at all is `ui.show_parent_thread_button`, default `false`, read once inside
`useParentThreadId` — both display sites (the button and the composer's empty-left-slot
test) go through that hook, so the flag has no second copy to disagree with. Only the
button is hidden: a server-sent `thread.open` still returns the user to the parent. `NewChat.tsx` resets kept transcripts, calls
`clear()` and navigates home. `LeftSidebar/ThreadHistory.tsx` pages threads and
navigates to `/thread/:id`; it refreshes on `firstInteraction` and reorders on a new
user message.

**Sidebar state.** The vendored shadcn provider has always written `sidebar:state` on
every toggle (`components/ui/sidebar.tsx`); the missing half was reading it back, which
`Page` now does through `lib/sidebarState.ts` —
`defaultOpen={resolveSidebarDefault(document.cookie, config.ui.default_sidebar_state)}`.
So the cookie is the user's last state and `default_sidebar_state` is only the default
for a browser that has never toggled; a value in that jar we did not write counts as
none. `defaultOpen` is a `useState` initialiser, so the read happens again on every
remount — which is what carries the state across `/` → `/thread/<id>`, where the flat
routes swap the whole page element. The lifetime is a year, one constant in
`ui/sidebar.tsx`: a "last state" that forgets itself after a quiet week is a surprise.
There is no `localStorage` key and no Recoil atom beside it — a second store for a value
the provider already persists would leave the cookie written and ignored. Two open tabs
may disagree until one of them toggles; accepted.

Mobile is deliberately outside all of that. `openMobile` is plain provider state, never
persisted: a modal sheet that reopened itself on load would be a defect, not a memory.
Instead one effect in `LeftSidebar` closes it on `useLocation().key` — every navigation
mints a fresh key, a push to the address already shown included, which is the case
`ThreadList`'s `<Link to="">` for the current thread produces. That one effect answers
for every way out of the sheet (a thread, a search hit, a delete that walks away), so
the call sites stay ignorant of it; only `NewChat` closes the sheet by hand, because a
new chat started from `/` does not navigate at all.

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
each: one socket per thread however often attached; opening without any HTTP call
first; a handshake naming the thread and nothing else; exactly one rebuild for a new
thread, old one closed; a second connection for a second `clear()` with no thread on
either side (`detach` forgets the descriptor); a `detach` cancelling a connection that
has not opened yet; a `detach` cancelling a _retry_ that has not fired yet (no socket
opens for a conversation nobody is in, and the generation fence would have hidden it);
queued work surviving a rebuild and flushing on `session.ready`;
`superseded` staying sticky through a re-attach; a 4403 being retried, not treated as
terminal; a `closed` transport reopening on a fresh attach; a blip healing without a new
attach; a new `chatProfile` reaching the next handshake without a reconnect; the device
riding along without becoming identity; the payload `threadId` beating the descriptor's;
sink-before-listeners ordering; listeners surviving a rebuild; and `onClose` reaching
the sink.

`frontend/tests/` runs under `frontend/vitest.config.ts` (jsdom,
`tests/setup-tests.ts`) and, because its `include` is `./**`, also covers
`chainlitSocket.spec.ts` — which imports `libs/react-client/src/socket.ts` by relative
path and guards hello-before-anything, buffer ordering, a reply kept across a
reconnect, a send from a `session.ready` listener staying behind the buffered ones,
the never-buffered `hb.ack`, backoff stopping on a terminal code, a refused upgrade
reported as never-opened, no reconnect after a deliberate close, both watchdog cases,
fan-out order, and `websocketUrl`. `unauthenticatedClose.spec.tsx` runs the whole chain
— a real `ChatTransport` over a stubbed `WebSocket`, driving the real `useChatSession`
in a `RecoilRoot` — and pins the link the fix is: a close 4401 leaves `userState` null,
while a 1006 leaves the signed-in user exactly where it was. Other specs cover
message-tree merging, ask-action
pruning, transcript freezing, wait messages, compact steps, icons, content rendering,
`NewChat`, `openThread` and `threadAddressSync` (both directions of the
address↔session loop). The account page has three: `schemaFormResolve.spec.ts` drives
the pure `resolveForm` against schemas `msgspec.json.schema` really emits,
`schemaForm.spec.tsx` renders the controls it resolves, and `accountPage.spec.tsx`
stubs `SchemaForm` to pin what the page alone owns — the four fetch states, what it
hands the form, and that a save puts the values back, re-renders from the response and
raises exactly one toast either way. `userNavAccount.spec.tsx` guards the reduced user
menu: the row appears only for a configured account page, prefers the configured title,
navigates to `/account`, and no longer grows rows for anything else. `displayModePrecedence.spec.ts`, whose three failures were the
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
