"""Fetches per-call agent config from the dashboard and applies locked blocks.

The room.metadata payload set by the dashboard's /api/livekit/token route
carries `{"configId": "<uuid>"}`. The worker fetches the matching
AgentConfig over HTTP, re-injects the locked compliance blocks server-side,
and exposes a small dataclass with the tunable runtime values.

Falls back silently to hardcoded defaults if the fetch fails — the call
must keep running even if the dashboard is unreachable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from locked_blocks import apply_locked_blocks

logger = logging.getLogger("agent.config")

DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """Per-call values applied on top of hardcoded worker defaults."""

    config_id: str | None
    name: str | None
    system_prompt: str
    opener_text: str
    temperature: float
    voice_speed: float
    max_call_duration_s: int
    silence_reprompt_text: str

    @classmethod
    def fallback(
        cls,
        *,
        system_prompt: str,
        opener_text: str,
        silence_reprompt_text: str,
    ) -> "AgentRuntimeConfig":
        return cls(
            config_id=None,
            name=None,
            system_prompt=apply_locked_blocks(system_prompt),
            opener_text=opener_text,
            temperature=0.7,
            voice_speed=0.95,
            max_call_duration_s=180,
            silence_reprompt_text=silence_reprompt_text,
        )


async def fetch_config(config_id: str) -> dict | None:
    """GET dashboard `/api/agent-configs/:id`. Returns parsed JSON or None on any failure."""
    base = os.getenv("DASHBOARD_API_BASE")
    token = os.getenv("DASHBOARD_API_TOKEN")
    if not base or not token:
        logger.warning("dashboard env missing — using fallback config")
        return None

    url = f"{base.rstrip('/')}/api/agent-configs/{config_id}"
    headers = {"authorization": f"Bearer {token}"}

    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "config fetch non-200: status=%s attempt=%s body=%s",
                resp.status_code,
                attempt,
                resp.text[:200],
            )
            if resp.status_code in (401, 403, 404):
                return None
        except httpx.HTTPError as e:
            logger.warning("config fetch error attempt=%s: %s", attempt, e)
    return None


async def load_runtime_config(
    config_id: str | None,
    *,
    default_system_prompt: str,
    default_opener_text: str,
    default_silence_reprompt_text: str,
) -> AgentRuntimeConfig:
    """Fetch + merge with hardcoded defaults, always re-injecting locked blocks."""
    fallback = AgentRuntimeConfig.fallback(
        system_prompt=default_system_prompt,
        opener_text=default_opener_text,
        silence_reprompt_text=default_silence_reprompt_text,
    )
    if not config_id:
        return fallback

    raw = await fetch_config(config_id)
    if not raw:
        return fallback

    try:
        return AgentRuntimeConfig(
            config_id=raw.get("id"),
            name=raw.get("name"),
            system_prompt=apply_locked_blocks(str(raw["systemPrompt"])),
            opener_text=str(raw["openerText"]),
            temperature=float(raw.get("temperature", fallback.temperature)),
            voice_speed=float(raw.get("voiceSpeed", fallback.voice_speed)),
            max_call_duration_s=int(
                raw.get("maxCallDurationS", fallback.max_call_duration_s)
            ),
            silence_reprompt_text=str(
                raw.get("silenceRepromptText", fallback.silence_reprompt_text)
            ),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("config payload malformed — using fallback: %s", e)
        return fallback
