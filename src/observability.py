"""Structured logging, call-summary tracking, and CRM webhook delivery.

Replaces ad-hoc `logger.info("[QUALIFIED] ...")` calls with JSON-line events
that downstream systems (Coolify log driver, n8n CRM webhook) can ingest.

Each call gets a stable `call_id` injected into every log record so a single
session can be reconstructed from interleaved worker logs.

The webhook poster is fire-and-forget with bounded retry: a failing CRM should
never block the call from finishing or stall the agent's response loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("agent")

WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "").strip()
WEBHOOK_TIMEOUT_S = float(os.getenv("N8N_WEBHOOK_TIMEOUT_S", "5.0"))
WEBHOOK_MAX_RETRIES = int(os.getenv("N8N_WEBHOOK_MAX_RETRIES", "3"))
FAILED_WEBHOOK_LOG = os.getenv(
    "N8N_WEBHOOK_FAILURE_LOG", "/tmp/lisa-crm-failed-writes.jsonl"
)
# Dashboard's /api/voice-outcome route guards with verifyBearer("VOICE_OUTCOME_SECRET",
# "x-voice-secret"). Without this header, the dashboard returns 401 and our retry
# loop bails (status<500 = no retry) → caller_hangup / qualified webhooks silently
# disappear and Lead.status stays 'calling' forever. Send the bearer in both header
# variants so the same env var works against either check path.
WEBHOOK_AUTH_TOKEN = os.getenv("VOICE_OUTCOME_SECRET", "").strip()


def log_event(call_id: str, event: str, **fields: Any) -> None:
    """Emit a single JSON-line event on the standard logger.

    Format is intentionally flat so jq / log search works without unpacking.
    """
    payload = {
        "ts": round(time.time(), 3),
        "call_id": call_id,
        "event": event,
        **fields,
    }
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))


def new_call_id(room_name: str) -> str:
    """Generate a call_id that's unique per session and grep-friendly."""
    return f"{room_name}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


@dataclass
class CallSummary:
    """Aggregates per-call lifecycle metrics for emission at session end."""

    call_id: str
    room: str
    started_at: float = field(default_factory=time.time)
    user_turns: int = 0
    agent_turns: int = 0
    final_state: str = "unknown"
    final_reason: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    watchdog_triggers: int = 0
    webhook_failures: int = 0
    time_to_first_audio_ms: int | None = None

    def record_error(self, source: str, error: str) -> None:
        self.errors.append({"source": source, "error": error, "ts": time.time()})

    def emit(self) -> None:
        log_event(
            self.call_id,
            "call_summary",
            room=self.room,
            duration_s=round(time.time() - self.started_at, 2),
            user_turns=self.user_turns,
            agent_turns=self.agent_turns,
            final_state=self.final_state,
            final_reason=self.final_reason,
            errors=self.errors,
            watchdog_triggers=self.watchdog_triggers,
            webhook_failures=self.webhook_failures,
            time_to_first_audio_ms=self.time_to_first_audio_ms,
        )


async def _post_webhook_with_retry(
    url: str, payload: dict[str, Any], call_id: str, summary: CallSummary | None
) -> None:
    """Post to CRM webhook with exponential backoff. Last-resort: jsonl spool."""
    backoff = 1.0
    headers: dict[str, str] = {}
    if WEBHOOK_AUTH_TOKEN:
        headers["x-voice-secret"] = WEBHOOK_AUTH_TOKEN
        headers["authorization"] = f"Bearer {WEBHOOK_AUTH_TOKEN}"
    for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code < 500:
                    if resp.status_code >= 400:
                        log_event(
                            call_id,
                            "webhook_client_error",
                            status=resp.status_code,
                            event_type=payload.get("event"),
                        )
                    return  # 2xx/3xx/4xx = no retry
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log_event(
                call_id,
                "webhook_attempt_failed",
                attempt=attempt,
                error=type(e).__name__,
            )
        await asyncio.sleep(backoff)
        backoff *= 2

    # Spool to disk for later replay so the lead is never lost.
    try:
        with open(FAILED_WEBHOOK_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"call_id": call_id, "payload": payload}) + "\n")
    except OSError as e:
        log_event(call_id, "webhook_spool_failed", error=str(e))
    if summary is not None:
        summary.webhook_failures += 1
    log_event(call_id, "webhook_exhausted_spooled", event_type=payload.get("event"))


def fire_webhook(
    call_id: str,
    event: str,
    payload: dict[str, Any],
    summary: CallSummary | None = None,
) -> None:
    """Schedule a CRM webhook delivery without awaiting it.

    Tools (mark_qualified_send_email, schedule_callback, mark_not_qualified)
    must return immediately so the agent stays responsive — the webhook
    runs in the background and any failure is spooled to disk.
    """
    if not WEBHOOK_URL:
        log_event(call_id, "webhook_skipped_no_url", event_type=event)
        return

    body = {"event": event, "call_id": call_id, **payload}
    asyncio.create_task(
        _post_webhook_with_retry(WEBHOOK_URL, body, call_id, summary),
        name=f"webhook-{event}-{call_id}",
    )
