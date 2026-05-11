# CONTEXT — Fachwelt Outbound Voice Agent

Domain glossary. Use these terms exactly when naming modules, types, and tests.
Architecture vocabulary (Module / Interface / Seam / Adapter / Depth) lives in
`~/.claude/skills/improve-codebase-architecture/LANGUAGE.md`.

## Domain terms

- **Call** — a single outbound phone session orchestrated by the worker. One
  Call has one room, one `call_id`, one `CallSummary`, one `RuntimeConfig`,
  and runs from connect to shutdown callback.

- **CallSummary** — per-call lifecycle aggregate emitted as one structured
  log line at session end. Mutated by tools, watchdog, silence watcher, and
  session-error listener. Final state is one of `qualified | not_qualified |
  callback | technical_callback | user_hangup | unknown`.

- **RuntimeConfig** (`AgentRuntimeConfig`) — per-call config carried in
  `room.metadata`, parsed and merged with worker defaults. Tunable knobs:
  prompt, opener text, temperature, voice speed, max duration, silence
  reprompt text. The dashboard is the source.

- **LockedBlock** — a compliance-critical span of the system prompt
  (KI/recording disclosure, hard speaking-style rules, tool sequence). Lives
  server-side as canonical Python. Every Call's prompt has all LockedBlocks
  re-injected so client-side tampering cannot strip disclosure.

- **LockedPrompt** — a system prompt with all LockedBlocks guaranteed
  present. Constructed only via `LockedPrompt.from_raw`; the type's
  `__post_init__` enforces the invariant. The single seam between raw
  dashboard prompt strings and what the LLM sees.

- **Watchdog** (`CallWatchdog`) — short-circuits silent-stuck failures
  during a Call. Two state machines: speaking-stuck (TTS wedged) and
  llm-stuck (LLM never replies). On trigger: live-TTS apology, mark
  `technical_callback`, close session.

- **SilenceWatcher** — re-prompts once on real silence after the opener,
  hangs up on persistent dead air. Event-driven via the
  `user_input_transcribed` listener (not pure polling).

- **Opener** — the first utterance of a Call. Pre-rendered MP3 for the
  default text (zero TTS variance), live TTS for custom openers. A preflight
  validates the MP3 before `session.start`.

- **Tool** — one of three function-tools the agent can call:
  `mark_qualified_send_email`, `schedule_callback`, `mark_not_qualified`.
  Each one stamps a final state on the CallSummary and fires a CRM webhook.

- **CRM Webhook** — fire-and-forget POST to n8n with bounded retry +
  exponential backoff. Last-resort: spool to disk. Never blocks the agent
  reply loop.
