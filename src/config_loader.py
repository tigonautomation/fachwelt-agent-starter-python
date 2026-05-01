"""Per-call agent config carried in `room.metadata`.

The dashboard's /api/livekit/token route packs the full AgentConfig payload
into room.metadata as `{"role": "operator", "config": {...}}`. The worker
parses that here, re-injects locked compliance blocks server-side, and
exposes a small dataclass with the tunable runtime values.

Falls back silently to hardcoded defaults if metadata is missing or
malformed — the call must keep running.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from locked_blocks import apply_locked_blocks

logger = logging.getLogger("agent.config")


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


def parse_metadata_config(raw_metadata: str | None) -> dict | None:
    """Extract the `config` dict from room.metadata JSON. None on any failure."""
    if not raw_metadata:
        return None
    try:
        parsed = json.loads(raw_metadata)
    except ValueError as e:
        logger.warning("metadata json parse failed: %s", e)
        return None
    if not isinstance(parsed, dict):
        return None
    cfg = parsed.get("config")
    return cfg if isinstance(cfg, dict) else None


def load_runtime_config(
    raw_metadata: str | None,
    *,
    default_system_prompt: str,
    default_opener_text: str,
    default_silence_reprompt_text: str,
) -> AgentRuntimeConfig:
    """Parse metadata + merge with hardcoded defaults, always re-injecting locked blocks."""
    fallback = AgentRuntimeConfig.fallback(
        system_prompt=default_system_prompt,
        opener_text=default_opener_text,
        silence_reprompt_text=default_silence_reprompt_text,
    )
    cfg = parse_metadata_config(raw_metadata)
    if not cfg:
        return fallback

    try:
        return AgentRuntimeConfig(
            config_id=cfg.get("id"),
            name=cfg.get("name"),
            system_prompt=apply_locked_blocks(str(cfg["systemPrompt"])),
            opener_text=str(cfg["openerText"]),
            temperature=float(cfg.get("temperature", fallback.temperature)),
            voice_speed=float(cfg.get("voiceSpeed", fallback.voice_speed)),
            max_call_duration_s=int(
                cfg.get("maxCallDurationS", fallback.max_call_duration_s)
            ),
            silence_reprompt_text=str(
                cfg.get("silenceRepromptText", fallback.silence_reprompt_text)
            ),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("config payload malformed — using fallback: %s", e)
        return fallback
