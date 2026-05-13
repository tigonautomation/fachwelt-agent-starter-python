"""Gap 2 — Health classifier unit tests.

The classifier is a pure function of CallSummary state, so we drive it with
constructed summaries instead of running a live session. Each test pins one
classification branch to one PRD rule (§5 Gap 2) so a regression hits a
specific test rather than a vague "classify is wrong".
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from call_health import (
    HARD_FAIL,
    HEALTHY,
    NORMAL_NO_PICKUP,
    SOFT_FAIL,
    classify,
    emit_health,
)
from observability import CallSummary


def _summary(**overrides) -> CallSummary:
    s = CallSummary(call_id="c-h", room="r")
    # Long enough so the no-pickup-short-duration branch is not auto-taken.
    s.started_at = time.time() - 30
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_healthy_when_both_speak_and_terminal_clean() -> None:
    s = _summary(user_turns=4, agent_turns=5, final_state="qualified")
    v = classify(s)
    assert v.classification == HEALTHY
    assert v.signals == []


def test_normal_no_pickup_short_silent_call() -> None:
    s = _summary(user_turns=0, agent_turns=0, final_state="caller_hangup")
    s.started_at = time.time() - 1.0  # < NO_PICKUP_DURATION_S
    v = classify(s)
    assert v.classification == NORMAL_NO_PICKUP


def test_hard_fail_technical_callback_state() -> None:
    s = _summary(
        user_turns=2, agent_turns=3, final_state="technical_callback"
    )
    v = classify(s)
    assert v.classification == HARD_FAIL
    assert "final_state_technical_callback" in v.signals


def test_hard_fail_agent_silent_past_no_pickup_window() -> None:
    """Callee picked up (or call ran long) but agent never spoke — worker fault."""
    s = _summary(user_turns=0, agent_turns=0, final_state="caller_hangup")
    s.started_at = time.time() - 20.0  # well past NO_PICKUP_DURATION_S
    v = classify(s)
    assert v.classification == HARD_FAIL
    assert "agent_silent_after_pickup" in v.signals


def test_hard_fail_opener_preflight_recorded_as_error() -> None:
    s = _summary(user_turns=0, agent_turns=0, final_state="caller_hangup")
    s.record_error(source="opener_audio", error="invalid mp3 header")
    v = classify(s)
    assert v.classification == HARD_FAIL
    assert "opener_preflight_failed" in v.signals


def test_soft_fail_when_only_webhook_failed() -> None:
    s = _summary(user_turns=3, agent_turns=4, final_state="qualified", webhook_failures=1)
    v = classify(s)
    assert v.classification == SOFT_FAIL
    assert v.signals == ["webhook_failures_1"]


def test_hard_fail_outranks_soft_fail() -> None:
    """Both hard and soft signals present → HARD_FAIL wins, but soft signals
    are preserved in the signals list so n8n can show full context."""
    s = _summary(
        user_turns=0,
        agent_turns=0,
        final_state="technical_callback",
        webhook_failures=2,
    )
    v = classify(s)
    assert v.classification == HARD_FAIL
    assert "final_state_technical_callback" in v.signals
    assert "webhook_failures_2" in v.signals


def test_emit_health_writes_structured_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="agent")
    s = _summary(user_turns=2, agent_turns=3, final_state="qualified")
    v = emit_health(s)
    rec = next(r for r in caplog.records if "call_health" in r.getMessage())
    payload = json.loads(rec.getMessage())
    assert payload["event"] == "call_health"
    assert payload["classification"] == v.classification
    assert payload["final_state"] == "qualified"
