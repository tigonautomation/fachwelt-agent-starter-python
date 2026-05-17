# AGENTS.md

> **REQUIRED READING vor jeder Session am Worker:** [`../outbound-agent-prototype/docs/prd-launch-readiness.md`](../outbound-agent-prototype/docs/prd-launch-readiness.md) — Launch-Gate-Scope für Fachwelt Outbound, Locked Decisions, Customer-Klärungsliste.

## Session-Stand (2026-05-13)

Phase: Pre-Launch. Worker production-hardened + Gap 2/3 + Architecture-Deepening abgeschlossen. Branch `edon/qa-sweep-fixes` → Coolify Worker Staging.

Neue Module seit 2026-04-30 Hardening:
- `src/call_health.py` — Per-Call Health-Classifier (`HARD_FAIL` / `SOFT_FAIL` / `NORMAL_NO_PICKUP` / `HEALTHY`). `emit_health()` läuft in `_finalize` nach `summary.emit()`. Posten via Log-Line `call_health` für n8n-Konsum.
- `CallSummary.turns` + `record_turn()` + `MAX_TRANSCRIPT_TURNS=200` — Transcript-Buffer. Snapshot in jedes Terminal-Webhook-Payload (ToolInvoked / SilenceHangup / CallerHungUp / WatchdogTriggered).
- `UserTurnFinal.text` / `AgentTurn.text` carry STT/LLM-Text durch Event-Sink. `LogSink` emittet `turn` event mit `text_len` (kein Content — DSGVO).

Deepening-Fixes 2026-05-13:
- `CallSession._started` flag → `final_state = "startup_aborted"` bei `session.start`-Crash. Classifier mappt das auf HARD_FAIL.
- Counter/Transcript-Lockstep in `SummarySink`: empty STT-Text skippt counter (sonst False-Negative auf `agent_silent_after_pickup`).
- Finalize-Ordering pinned per `assert self._summary.final_state and != "unknown"` vor `summary.emit()`.
- `LockedPrompt.__post_init__` validiert END-Marker-Count (rejects tampered prompts).
- `_NullSink` raus, `RecordingSink` als Default für FachweltAssistant ohne expliziten Sink (tests können events inspizieren).

Tests: 32 non-LLM passing (7 LLM-API tests fail ohne Creds — pre-existing, nicht in CI). Run: `uv run pytest --ignore=tests/test_conversations.py --ignore=tests/test_agent.py -q`.

Next: 3 Real-Mic Test-Calls auf Edon-Linie (Launch-Gate Pre-Condition, Gap 6 in PRD).

This is a LiveKit Agents project. LiveKit Agents is a Python SDK for building voice AI agents. This project is intended to be used with LiveKit Cloud. See @README.md for more about the rest of the LiveKit ecosystem.

The following is a guide for working with this project.

## Project structure

This Python project uses the `uv` package manager. You should always use `uv` to install dependencies, run the agent, and run tests.

All app-level code is in the `src/` directory. In general, simple agents can be constructed with a single `agent.py` file. Additional files can be added, but you must retain `agent.py` as the entrypoint (see the associated Dockerfile for how this is deployed).

Be sure to maintain code formatting. You can use the ruff formatter/linter as needed: `uv run ruff format` and `uv run ruff check`.

## LiveKit Documentation

LiveKit Agents is a fast-evolving project, and the documentation is updated frequently. You should always refer to the latest documentation when working with this project. For your convenience, LiveKit offers both a CLI and an MCP server that can be used to browse and search its documentation. If the developer has not yet installed the CLI, you should recommend that they install it.

### LiveKit CLI

The [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) `lk docs` subcommand gives full access to LiveKit documentation from the terminal. Requires CLI version 2.15.0+. Check with `lk --version`.

Install or update the CLI:

- **macOS:** `brew install livekit-cli` (update: `brew update && brew upgrade livekit-cli`)
- **Linux:** `curl -sSL https://get.livekit.io/cli | bash`
- **Windows:** `winget install LiveKit.LiveKitCLI` (update: `winget upgrade LiveKit.LiveKitCLI`)

Run `lk docs --help` to see all available commands. Key commands: `lk docs overview`, `lk docs search`, `lk docs get-page`, `lk docs code-search`, `lk docs changelog`, `lk docs submit-feedback`. Run `lk docs <command> --help` before using a command for the first time, as the help text includes advice on usage and interpreting results. Prefer browsing (`overview`, `get-page`) over search, and prefer `search` over `code-search`, as docs pages are more up-to-date and provide better context than raw code.

### LiveKit Docs MCP Server

For deeper IDE integration, LiveKit also offers an [MCP server](https://docs.livekit.io/reference/developer-tools/docs-mcp/) with the same documentation access. It uses Streamable HTTP transport at `https://docs.livekit.io/mcp`. Refer to your IDE's documentation for how to add it.

### Submit docs feedback to LiveKit

If you use the LiveKit CLI or MCP Server to search or browse documentation, also submit constructive feedback to LiveKit with `lk docs submit-feedback` or the MCP `submit_docs_feedback` tool. Add this to your to-do list when planning work, to ensure you file feedback after completing your other tasks. Useful feedback includes incomplete documentation, broken examples, missing steps or any other documentation gap or issue.

## Handoffs and tasks ("workflows")

Voice AI agents are highly sensitive to excessive latency. For this reason, it's important to design complex agents in a structured manner that minimizes the amount of irrelevant context and unnecessary tools included in requests to the LLM. LiveKit Agents supports handoffs (one agent hands control to another) and tasks (tightly-scoped prompts to achieve a specific outcome) to support building reliable workflows. You should make use of these features, instead of writing long instruction prompts that cover multiple phases of a conversation.  Refer to the [documentation](https://docs.livekit.io/agents/build/workflows/) for more information.

## Testing

When possible, add tests for agent behavior. Read the [documentation](https://docs.livekit.io/agents/start/testing/), and refer to existing tests in the `tests/` directory.  Run tests with `uv run pytest`.

Important: When modifying core agent behavior such as instructions, tool descriptions, and tasks/workflows/handoffs, never just guess what will work. Always use test-driven development (TDD) and begin by writing tests for the desired behavior. For instance, if you're planning to add a new tool, write one or more tests for the tool's behavior, then iterate on the tool until the tests pass correctly. This will ensure you are able to produce a working, reliable agent for the user.

## LiveKit CLI

Beyond documentation access, the LiveKit CLI (`lk`) supports other tasks such as managing SIP trunks for telephony-based agents. Run `lk --help` to explore available commands.
