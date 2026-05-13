"""Block C/D/F resilience unit tests.

These cover the watchdogs, structured event logger, call-summary aggregation,
and CRM webhook delivery. They run without a live LiveKit room — failures are
injected at the seam between agent.py and the support modules.

Why unit tests instead of integration: reproducing TTS/STT/LLM mid-call drops
requires real network manipulation (pfctl/tcpkill). The stuck-state recovery
*logic* is fully testable here; the network-drop reproduction belongs in the
manual E2E run before first real outbound calls (see Block E checklist).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import pytest

from call_event_sink import (
    AgentTurn,
    CallerHungUp,
    SilenceHangup,
    SummarySink,
    ToolInvoked,
    UserTurnFinal,
    WatchdogTriggered,
    WebhookSink,
    production_sink,
)
from observability import (
    MAX_TRANSCRIPT_TURNS,
    CallSummary,
    fire_webhook,
    log_event,
    new_call_id,
)

# ─────────────────────────────────────────────────────────────────────────────
# D19/D20/D21 — observability primitives
# ─────────────────────────────────────────────────────────────────────────────


def test_new_call_id_is_unique_and_includes_room() -> None:
    a = new_call_id("room-abc")
    b = new_call_id("room-abc")
    assert a != b
    assert a.startswith("room-abc-")
    # shape: room-<unix_ts>-<uuid_prefix>
    parts = a.split("-")
    assert len(parts) >= 3


def test_log_event_emits_single_json_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="agent")
    log_event("call-1", "tool_qualified", reason="email_confirmed", email="x@y.de")
    rec = next(r for r in caplog.records if r.name == "agent")
    payload = json.loads(rec.getMessage())
    assert payload["call_id"] == "call-1"
    assert payload["event"] == "tool_qualified"
    assert payload["email"] == "x@y.de"
    assert "ts" in payload


def test_call_summary_emits_expected_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="agent")
    s = CallSummary(call_id="call-2", room="r")
    s.user_turns = 4
    s.agent_turns = 5
    s.final_state = "qualified"
    s.final_reason = "email_confirmed"
    s.record_error(source="elevenlabs", error="ws closed")
    s.emit()

    summary_record = next(
        r for r in caplog.records if "call_summary" in r.getMessage()
    )
    payload = json.loads(summary_record.getMessage())
    assert payload["event"] == "call_summary"
    assert payload["user_turns"] == 4
    assert payload["agent_turns"] == 5
    assert payload["final_state"] == "qualified"
    assert payload["errors"][0]["source"] == "elevenlabs"
    assert payload["duration_s"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# Transcript capture (Gap 3 — persist user/agent turn text via sink → webhook)
# ─────────────────────────────────────────────────────────────────────────────


def test_record_turn_appends_role_text_and_offset() -> None:
    s = CallSummary(call_id="c-t1", room="r")
    s.record_turn("user", "Hallo")
    s.record_turn("agent", "Guten Tag")
    assert len(s.turns) == 2
    assert s.turns[0]["role"] == "user"
    assert s.turns[0]["text"] == "Hallo"
    assert s.turns[1]["role"] == "agent"
    assert "ts_offset" in s.turns[0]
    assert s.transcript_truncated is False


def test_record_turn_skips_empty_and_caps() -> None:
    s = CallSummary(call_id="c-t2", room="r")
    s.record_turn("user", "")
    s.record_turn("user", "   ")
    assert s.turns == []
    for i in range(MAX_TRANSCRIPT_TURNS + 5):
        s.record_turn("user", f"t{i}")
    assert len(s.turns) == MAX_TRANSCRIPT_TURNS
    assert s.transcript_truncated is True


def test_summary_sink_translates_turn_events() -> None:
    s = CallSummary(call_id="c-t3", room="r")
    sink = SummarySink(s)
    sink.emit(UserTurnFinal(text="ja gerne"))
    sink.emit(AgentTurn(text="Super, ich notiere das."))
    sink.emit(UserTurnFinal(text=""))  # ignored: counter + transcript both skip
    assert s.user_turns == 1
    assert s.agent_turns == 1
    assert [t["role"] for t in s.turns] == ["user", "agent"]
    assert s.turns[1]["text"] == "Super, ich notiere das."


class _CapturingWebhook:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self,
        call_id: str,
        event: str,
        payload: dict[str, Any],
        summary: CallSummary | None = None,
    ) -> None:
        self.calls.append((event, payload))


def test_webhook_sink_attaches_turns_to_terminal_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = CallSummary(call_id="c-t4", room="r")
    s.record_turn("user", "ja")
    s.record_turn("agent", "perfekt")
    cap = _CapturingWebhook()
    monkeypatch.setattr("call_event_sink.fire_webhook", cap)
    sink = WebhookSink("c-t4", s)

    sink.emit(ToolInvoked(state="qualified", reason="email_confirmed", fields={"email": "a@b.de"}))
    sink.emit(SilenceHangup(reason="no_response_after_opener"))
    sink.emit(CallerHungUp(reason="caller_disconnect"))
    sink.emit(WatchdogTriggered(kind="llm_stuck", elapsed=12.0, threshold=10.0))

    for _event, payload in cap.calls:
        assert payload["turns"] == s.turns
        assert payload["transcript_truncated"] is False
    # Snapshot semantics: mutating summary.turns later doesn't change past payloads.
    s.record_turn("user", "nachgereicht")
    for _event, payload in cap.calls:
        assert all(t["text"] != "nachgereicht" for t in payload["turns"])


# ─────────────────────────────────────────────────────────────────────────────
# D22 — webhook fire-and-forget + spool on failure
# ─────────────────────────────────────────────────────────────────────────────


async def test_fire_webhook_no_url_logs_skip(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="agent")
    monkeypatch.setattr("observability.WEBHOOK_URL", "")
    fire_webhook("call-3", "qualified", {"email": "a@b.de"})
    # no background task created when URL missing
    msgs = [r.getMessage() for r in caplog.records]
    assert any("webhook_skipped_no_url" in m for m in msgs)


async def test_fire_webhook_spools_on_total_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When all retries fail, payload is appended to the failure-spool jsonl."""
    caplog.set_level(logging.INFO, logger="agent")
    spool_path = tmp_path / "failed.jsonl"
    monkeypatch.setattr("observability.WEBHOOK_URL", "http://127.0.0.1:1/never")
    monkeypatch.setattr("observability.WEBHOOK_MAX_RETRIES", 1)
    monkeypatch.setattr("observability.WEBHOOK_TIMEOUT_S", 0.05)
    monkeypatch.setattr("observability.FAILED_WEBHOOK_LOG", str(spool_path))

    summary = CallSummary(call_id="call-4", room="r")
    fire_webhook("call-4", "callback", {"reason": "no_response"}, summary=summary)

    # Wait briefly for the background task to finish.
    for _ in range(40):
        await asyncio.sleep(0.05)
        if spool_path.exists():
            break

    assert spool_path.exists(), "expected payload to be spooled to disk"
    spooled = json.loads(spool_path.read_text().strip())
    assert spooled["call_id"] == "call-4"
    assert spooled["payload"]["event"] == "callback"
    assert summary.webhook_failures == 1


# ─────────────────────────────────────────────────────────────────────────────
# F27/F28 — watchdog state machine
# ─────────────────────────────────────────────────────────────────────────────


class _FakeSession:
    """Minimal stand-in for AgentSession so we can drive the watchdog directly."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}
        self.user_state = "listening"
        self.agent_state = "idle"
        self.say_called_with: str | None = None
        self.aclose_called = False

    def on(self, event: str, callback: Any) -> Any:
        self._handlers.setdefault(event, []).append(callback)
        return callback

    def emit(self, event: str, payload: Any) -> None:
        for cb in self._handlers.get(event, []):
            cb(payload)

    def say(self, text: str, *, allow_interruptions: bool = True) -> Any:
        self.say_called_with = text
        fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()
        fut.set_result(None)
        return fut

    async def aclose(self) -> None:
        self.aclose_called = True


class _StateChange:
    def __init__(self, old: str, new: str) -> None:
        self.old_state = old
        self.new_state = new


async def test_watchdog_speaking_stuck_triggers_recovery(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """F27 — agent stuck in `speaking` state past threshold runs recovery + closes."""
    caplog.set_level(logging.INFO, logger="agent")
    import watchdog as wd

    monkeypatch.setattr(wd, "SPEAKING_STUCK_THRESHOLD_S", 0.2)
    monkeypatch.setattr(wd, "POLL_INTERVAL_S", 0.05)

    session = _FakeSession()
    summary = CallSummary(call_id="call-w1", room="r")
    w = wd.CallWatchdog(session=session, sink=production_sink("call-w1", summary))
    w.start()

    session.emit("agent_state_changed", _StateChange("idle", "speaking"))
    await asyncio.sleep(1.0)

    assert summary.watchdog_triggers == 1
    assert summary.final_state == "technical_callback"
    assert summary.final_reason == "speaking_stuck"
    assert session.say_called_with == wd.RECOVERY_UTTERANCE
    assert session.aclose_called
    w.stop()


async def test_watchdog_llm_stuck_triggers_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F28 — user finished talking, agent never replied past threshold."""
    import watchdog as wd

    monkeypatch.setattr(wd, "LLM_STUCK_THRESHOLD_S", 0.2)
    monkeypatch.setattr(wd, "POLL_INTERVAL_S", 0.05)

    session = _FakeSession()
    summary = CallSummary(call_id="call-w2", room="r")
    w = wd.CallWatchdog(session=session, sink=production_sink("call-w2", summary))
    w.start()

    session.emit("user_state_changed", _StateChange("speaking", "listening"))
    await asyncio.sleep(1.0)

    assert summary.watchdog_triggers == 1
    assert summary.final_reason == "llm_stuck"
    w.stop()


async def test_watchdog_does_not_fire_when_agent_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity — happy path doesn't trigger spurious watchdog events."""
    import watchdog as wd

    monkeypatch.setattr(wd, "SPEAKING_STUCK_THRESHOLD_S", 0.4)
    monkeypatch.setattr(wd, "POLL_INTERVAL_S", 0.05)

    session = _FakeSession()
    summary = CallSummary(call_id="call-w3", room="r")
    w = wd.CallWatchdog(session=session, sink=production_sink("call-w3", summary))
    w.start()

    session.emit("agent_state_changed", _StateChange("idle", "speaking"))
    await asyncio.sleep(0.15)
    session.emit("agent_state_changed", _StateChange("speaking", "listening"))
    await asyncio.sleep(0.8)

    assert summary.watchdog_triggers == 0
    assert not session.aclose_called
    w.stop()


# ─────────────────────────────────────────────────────────────────────────────
# F30 — health endpoint
# ─────────────────────────────────────────────────────────────────────────────


async def test_health_endpoint_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness probe contract: 200 + JSON body with status + active_sessions."""
    import socket

    import aiohttp

    from health import start_health_server

    # Pick an ephemeral port to avoid collisions with the production default.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    runner = await start_health_server(lambda: 7, port=port)
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 200
                payload = await resp.json()
        assert payload["status"] == "ok"
        assert payload["active_sessions"] == 7
        assert "uptime_s" in payload
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()
