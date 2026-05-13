"""Per-Call health classifier.

PRD Gap 2: every Call is classified at session-end so an external circuit
breaker (n8n) can decide whether to pause dispatch. Classification is a
pure function over `CallSummary` — never blocks the agent reply loop and
never depends on a network round-trip. The session-finalize path emits one
`call_health` log line per call; n8n ingests structured logs and posts to
the dashboard's `/api/admin/dispatch-pause` endpoint when its window
threshold trips.

Classification (from PRD §5 Gap 2):
  - HARD_FAIL: `final_state == "technical_callback"`, OR worker spoke nothing
    after a callee-pickup-shaped call (`agent_turns == 0` and duration past
    the no-pickup window), OR opener-audio preflight failure, OR a critical
    entrypoint error.
  - NORMAL_NO_PICKUP: no audio either way and the call was too short to
    have meant anything (typical Twilio busy/no-answer/voicemail-screen).
  - SOFT_FAIL: webhook deliveries failed (CRM write at risk; the call
    itself was fine).
  - HEALTHY: anything else.

`final_state == "technical_callback"` is the watchdog's terminal stamp — it
is the strongest single signal that the worker (not the carrier or the
callee) was the source of the failure, which is exactly what justifies
pausing dispatch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from observability import CallSummary, log_event

HEALTHY = "HEALTHY"
NORMAL_NO_PICKUP = "NORMAL_NO_PICKUP"
SOFT_FAIL = "SOFT_FAIL"
HARD_FAIL = "HARD_FAIL"

# A call is too short to have engaged the user. Under this, a zero-turn
# outcome is "callee never picked up" rather than worker fault. Twilio's BYE
# for busy/no-answer arrives well inside this window.
NO_PICKUP_DURATION_S = 5.0


@dataclass(frozen=True)
class HealthVerdict:
    classification: str
    signals: list[str]
    duration_s: float

    def as_payload(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "signals": list(self.signals),
            "duration_s": self.duration_s,
        }


def classify(summary: CallSummary) -> HealthVerdict:
    duration_s = round(time.time() - summary.started_at, 2)
    hard: list[str] = []
    soft: list[str] = []

    if summary.final_state == "technical_callback":
        hard.append("final_state_technical_callback")
    if summary.agent_turns == 0 and duration_s >= NO_PICKUP_DURATION_S:
        # Pickup happened (or call ran long) yet the agent never spoke. Either
        # the opener wedged or LLM/TTS never produced audio.
        hard.append("agent_silent_after_pickup")
    for err in summary.errors:
        if not isinstance(err, dict):
            continue
        src = err.get("source")
        if src == "opener_audio":
            hard.append("opener_preflight_failed")
        elif src == "entrypoint":
            hard.append("critical_error_entrypoint")

    if summary.webhook_failures > 0:
        soft.append(f"webhook_failures_{summary.webhook_failures}")

    if hard:
        return HealthVerdict(HARD_FAIL, hard + soft, duration_s)
    if (
        summary.agent_turns == 0
        and summary.user_turns == 0
        and duration_s < NO_PICKUP_DURATION_S
    ):
        return HealthVerdict(NORMAL_NO_PICKUP, ["no_pickup_short_duration"], duration_s)
    if soft:
        return HealthVerdict(SOFT_FAIL, soft, duration_s)
    return HealthVerdict(HEALTHY, [], duration_s)


def emit_health(summary: CallSummary) -> HealthVerdict:
    """Classify and emit a `call_health` log line. Returns verdict for callers
    who want to fan it out further (webhook, metrics)."""
    verdict = classify(summary)
    log_event(
        summary.call_id,
        "call_health",
        classification=verdict.classification,
        signals=verdict.signals,
        duration_s=verdict.duration_s,
        final_state=summary.final_state,
        webhook_failures=summary.webhook_failures,
    )
    return verdict
