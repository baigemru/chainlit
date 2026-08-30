# Documentation for Chainlit developers, contributors, and AI coding assistants.

Start with [AGENTS.md](../AGENTS.md) — what this fork is, the commands, and the
rules that govern changes. Then read the architecture documents below.

## Architecture

- [Backend](architecture/backend.md) — the Litestar application: package layout,
  the websocket transport and wire protocol, persistence, plugin and CLI.
- [Client](architecture/client.md) — the React frontend, `@chainlit/react-client`,
  the transport owner, and custom elements.

See also:

- [CONTRIBUTING.md](../CONTRIBUTING.md) — contribution guidelines
- [CHANGELOG.md](../CHANGELOG.md) — release history
- [context7.md](context7.md) — pre-resolved Context7 library IDs (partly stale;
  see the note in AGENTS.md)

## Research

Internal notes on CI, tooling, and test layout:

- [CI cache optimization](research/ci-cache-optimization.md)
- [Copilot type-checking](research/copilot-type-checking.md)
- [E2E parallel execution](research/e2e-parallel-execution.md)
