"""Centralized event routing for a Call.

Replaces hand-wired pairs of `log_event(...)` + `summary.field += 1` +
`fire_webhook(...)` scattered across the entrypoint, tools, watchdog, and
silence watcher. Every observable signal in a Call is a typed
`CallEvent`; the sink decides which fan-outs (log / summary / webhook)
apply.

Design:

  - `CallEvent` is a frozen dataclass hierarchy. Concrete subclasses
    name domain events ("ToolInvoked", "WatchdogTriggered", ...).
  - `CallEventSink` is the protocol. `emit(event)` is sync — LiveKit's
    `session.on(...)` listeners are sync, and any I/O fan-out (webhook)
    schedules its own background task.
  - `LogSink`, `SummarySink`, `WebhookSink` each handle one fan-out.
  - `CompositeSink` runs all of them in order.

Adding a new signal = adding one event type and one match arm in each
relevant sink. There is no second hand-wiring path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from observability import CallSummary, fire_webhook, log_event

# ─────────────────────────────────────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CallEvent:
    """Marker base for all Call lifecycle events."""


@dataclass(frozen=True)
class ToolInvoked(CallEvent):
    """A function-tool ran. Terminal disposition for the Call."""

    state: str  # "qualified" | "not_qualified" | "callback"
    reason: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionError(CallEvent):
    """LiveKit session emitted an error event (TTS / STT / LLM stream)."""

    source: str
    error: str


@dataclass(frozen=True)
class UserTurnFinal(CallEvent):
    """STT produced a final transcript for a user utterance."""

    text: str = ""


@dataclass(frozen=True)
class AgentTurn(CallEvent):
    """Conversation item added with role=assistant."""

    text: str = ""


@dataclass(frozen=True)
class WatchdogTriggered(CallEvent):
    kind: str  # "speaking_stuck" | "llm_stuck"
    elapsed: float
    threshold: float


@dataclass(frozen=True)
class SilenceReprompt(CallEvent):
    """Silence watcher hit the reprompt threshold."""


@dataclass(frozen=True)
class SilenceHangup(CallEvent):
    reason: str


@dataclass(frozen=True)
class CallerHungUp(CallEvent):
    """SIP caller disconnected (Twilio BYE). Terminal — close session + notify CRM."""

    reason: str


@dataclass(frozen=True)
class RecoverySayTimedOut(CallEvent):
    """Watchdog recovery utterance did not complete in time."""


@dataclass(frozen=True)
class RecoverySayFailed(CallEvent):
    error: str


@dataclass(frozen=True)
class OpenerPreflightFailed(CallEvent):
    error_type: str
    error: str


@dataclass(frozen=True)
class EntrypointException(CallEvent):
    error_type: str
    error: str


# ─────────────────────────────────────────────────────────────────────────────
# Sink protocol + concrete sinks
# ─────────────────────────────────────────────────────────────────────────────


class CallEventSink(Protocol):
    def emit(self, event: CallEvent) -> None: ...


class LogSink:
    """Translates events to structured log lines."""

    def __init__(self, call_id: str) -> None:
        self._call_id = call_id

    def emit(self, event: CallEvent) -> None:
        match event:
            case ToolInvoked(state=state, reason=reason, fields=fields):
                log_event(self._call_id, f"tool_{state}", reason=reason, **fields)
            case SessionError(source=source, error=error):
                log_event(self._call_id, "session_error", source=source, error=error)
            case WatchdogTriggered(kind=kind, elapsed=elapsed, threshold=threshold):
                log_event(
                    self._call_id,
                    f"watchdog_fired_{kind}",
                    elapsed=elapsed,
                    threshold=threshold,
                )
            case SilenceReprompt():
                log_event(self._call_id, "silence_reprompt_after_opener")
            case SilenceHangup():
                log_event(self._call_id, "silence_hangup_no_response")
            case CallerHungUp(reason=reason):
                log_event(self._call_id, "caller_hangup", reason=reason)
            case RecoverySayTimedOut():
                log_event(self._call_id, "recovery_say_timed_out")
            case RecoverySayFailed(error=error):
                log_event(self._call_id, "recovery_say_failed", error=error)
            case OpenerPreflightFailed(error_type=error_type, error=error):
                log_event(
                    self._call_id,
                    "opener_audio_preflight_failed",
                    error_type=error_type,
                    error=error,
                )
            case EntrypointException(error_type=error_type, error=error):
                log_event(
                    self._call_id,
                    "entrypoint_exception",
                    error_type=error_type,
                    error=error,
                )
            case UserTurnFinal(text=text):
                # Per-turn log line. Text *content* stays out (DSGVO + log
                # bloat), but length and ordinal go in so a stuck-session
                # post-mortem can correlate watchdog/health firings with the
                # turn that triggered them.
                log_event(
                    self._call_id, "turn", role="user", text_len=len(text or "")
                )
            case AgentTurn(text=text):
                log_event(
                    self._call_id, "turn", role="agent", text_len=len(text or "")
                )


class SummarySink:
    """Mutates the per-call summary based on event semantics."""

    def __init__(self, summary: CallSummary) -> None:
        self._summary = summary

    def emit(self, event: CallEvent) -> None:
        s = self._summary
        match event:
            case ToolInvoked(state=state, reason=reason):
                s.final_state = state
                s.final_reason = reason
            case SessionError(source=source, error=error):
                s.record_error(source=source, error=error)
            case UserTurnFinal(text=text):
                # Counter + transcript MUST stay in sync. The health classifier
                # uses `agent_turns == 0` as a hard-fail signal; if empty STT
                # finals inflated `user_turns` past zero while `turns` stayed
                # empty, a dead-air call could silently escape `HARD_FAIL`.
                if (text or "").strip():
                    s.user_turns += 1
                    s.record_turn("user", text)
            case AgentTurn(text=text):
                if (text or "").strip():
                    s.agent_turns += 1
                    s.record_turn("agent", text)
            case WatchdogTriggered(kind=kind):
                s.watchdog_triggers += 1
                s.final_state = "technical_callback"
                s.final_reason = kind
            case SilenceHangup(reason=reason):
                s.final_state = "callback"
                s.final_reason = reason
            case CallerHungUp(reason=reason):
                s.final_state = "caller_hangup"
                s.final_reason = reason
            case OpenerPreflightFailed(error=error):
                s.record_error(source="opener_audio", error=error)
            case EntrypointException(error=error):
                s.record_error(source="entrypoint", error=error)


class WebhookSink:
    """Fires CRM webhooks for terminal events. Background task; never blocks."""

    def __init__(self, call_id: str, summary: CallSummary) -> None:
        self._call_id = call_id
        self._summary = summary

    def emit(self, event: CallEvent) -> None:
        match event:
            case ToolInvoked(state=state, reason=reason, fields=fields):
                fire_webhook(
                    self._call_id,
                    state,
                    {"reason": reason, **fields, **self._transcript_payload()},
                    summary=self._summary,
                )
            case SilenceHangup(reason=reason):
                fire_webhook(
                    self._call_id,
                    "callback",
                    {"reason": reason, **self._transcript_payload()},
                    summary=self._summary,
                )
            case CallerHungUp(reason=reason):
                fire_webhook(
                    self._call_id,
                    "caller_hangup",
                    {"reason": reason, **self._transcript_payload()},
                    summary=self._summary,
                )
            case WatchdogTriggered(kind=kind, elapsed=elapsed, threshold=threshold):
                # Without this, watchdog recovery ends with no CRM notification
                # because _watch_caller_disconnect sees final_state=="technical_callback"
                # and skips its own CallerHungUp emit. Lead stays 'calling' until
                # recovery cron at 10min. Route through caller_hangup so dashboard
                # marks the lead failed (same disposition as a stuck timeout).
                fire_webhook(
                    self._call_id,
                    "caller_hangup",
                    {
                        "reason": f"watchdog_{kind}",
                        "elapsed": elapsed,
                        "threshold": threshold,
                        **self._transcript_payload(),
                    },
                    summary=self._summary,
                )

    def _transcript_payload(self) -> dict[str, Any]:
        # Snapshot of turns up to this terminal event. We copy the list so a
        # follow-on watchdog/hangup emit doesn't see a mutated reference.
        return {
            "turns": list(self._summary.turns),
            "transcript_truncated": self._summary.transcript_truncated,
        }


class CompositeSink:
    """Fans an event out to every registered sink, in order."""

    def __init__(self, sinks: list[CallEventSink]) -> None:
        self._sinks = sinks

    def emit(self, event: CallEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)


def production_sink(call_id: str, summary: CallSummary) -> CompositeSink:
    """Default wiring: log + summary + webhook fan-out."""
    return CompositeSink(
        [
            LogSink(call_id),
            SummarySink(summary),
            WebhookSink(call_id, summary),
        ]
    )


class RecordingSink:
    """Test fake. Captures every emitted event in order."""

    def __init__(self) -> None:
        self.events: list[CallEvent] = []

    def emit(self, event: CallEvent) -> None:
        self.events.append(event)
