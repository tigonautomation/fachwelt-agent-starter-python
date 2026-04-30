"""Watchdog tasks that catch silent stuck-state failures during a call.

Two failure modes both produce the same user experience — Lisa goes silent
mid-conversation, the user hears nothing, and "Hallo? Hallo?" follows. The
plugin retry budget is too long for phone calls (worst case ~36s), so these
watchdogs short-circuit much earlier with a graceful recovery.

  F27 — agent stuck in "speaking" state with no audio reaching the wire
        (ElevenLabs WS hang, TTS task wedged). Threshold: 10s.
  F28 — user finished talking, agent never replied (OpenAI 5xx storm,
        LLM task wedged). Threshold: 15s.

Both watchdogs operate by listening to AgentSession's `agent_state_changed`
events and arming asyncio timers when the session enters a risky state. The
recovery action speaks a brief apology in live-TTS (bypasses the wedged path),
flags the call as `technical_callback`, and lets the session close cleanly.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import AgentStateChangedEvent, UserStateChangedEvent

from observability import CallSummary, log_event

SPEAKING_STUCK_THRESHOLD_S = 10.0
LLM_STUCK_THRESHOLD_S = 15.0
POLL_INTERVAL_S = 0.5
RECOVERY_UTTERANCE = (
    "Entschuldigung, ich hatte gerade eine kurze Verbindungsstörung. "
    "Ich rufe Sie gleich nochmal zurück. Auf Wiederhören."
)


@dataclass
class _WatchdogState:
    speaking_since: float | None = None
    user_done_at: float | None = None
    triggered: bool = False


class CallWatchdog:
    """Per-session watchdog. Attach in the rtc_session entrypoint after start()."""

    def __init__(self, session: AgentSession, call_id: str, summary: CallSummary):
        self._session = session
        self._call_id = call_id
        self._summary = summary
        self._state = _WatchdogState()
        self._poll_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()

    def start(self) -> None:
        self._session.on("agent_state_changed", self._on_agent_state)
        self._session.on("user_state_changed", self._on_user_state)
        self._session.on("close", lambda *_: self.stop())
        self._poll_task = asyncio.create_task(self._poll_loop(), name="watchdog-poll")

    def stop(self) -> None:
        self._closed.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    def _on_agent_state(self, ev: AgentStateChangedEvent) -> None:
        now = time.time()
        if ev.new_state == "speaking":
            self._state.speaking_since = now
            self._state.user_done_at = None
        elif ev.old_state == "speaking":
            self._state.speaking_since = None
        if ev.new_state in ("listening", "idle") and self._state.user_done_at is None:
            # entering listening means the agent just finished — clear LLM-stuck timer
            self._state.user_done_at = None

    def _on_user_state(self, ev: UserStateChangedEvent) -> None:
        # User stopped speaking → agent should respond within LLM_STUCK_THRESHOLD_S.
        if ev.old_state == "speaking" and ev.new_state == "listening":
            self._state.user_done_at = time.time()
        elif ev.new_state == "speaking":
            # User started talking again before agent reply. No longer stuck on us.
            self._state.user_done_at = None

    async def _poll_loop(self) -> None:
        try:
            while not self._closed.is_set():
                await asyncio.sleep(POLL_INTERVAL_S)
                if self._state.triggered:
                    continue
                self._check_speaking_stuck()
                self._check_llm_stuck()
        except asyncio.CancelledError:
            pass

    def _check_speaking_stuck(self) -> None:
        started = self._state.speaking_since
        if started is None:
            return
        elapsed = time.time() - started
        if elapsed >= SPEAKING_STUCK_THRESHOLD_S:
            self._trigger(
                "speaking_stuck", elapsed=round(elapsed, 1), threshold=SPEAKING_STUCK_THRESHOLD_S
            )

    def _check_llm_stuck(self) -> None:
        done_at = self._state.user_done_at
        if done_at is None:
            return
        # Only consider stuck if agent isn't currently speaking
        if self._state.speaking_since is not None:
            return
        elapsed = time.time() - done_at
        if elapsed >= LLM_STUCK_THRESHOLD_S:
            self._trigger(
                "llm_stuck", elapsed=round(elapsed, 1), threshold=LLM_STUCK_THRESHOLD_S
            )

    def _trigger(self, kind: str, **details: float) -> None:
        self._state.triggered = True
        self._summary.watchdog_triggers += 1
        self._summary.final_state = "technical_callback"
        self._summary.final_reason = kind
        log_event(self._call_id, f"watchdog_fired_{kind}", **details)
        asyncio.create_task(self._recover(), name=f"watchdog-recover-{kind}")

    async def _recover(self) -> None:
        try:
            # Live-TTS bypasses any wedged audio iterator; even if TTS is broken
            # the say() call returns immediately and we proceed to close.
            handle = self._session.say(RECOVERY_UTTERANCE, allow_interruptions=False)
            try:
                await asyncio.wait_for(handle, timeout=8.0)
            except asyncio.TimeoutError:
                log_event(self._call_id, "recovery_say_timed_out")
        except Exception as e:
            log_event(self._call_id, "recovery_say_failed", error=str(e))
        finally:
            await self._session.aclose()
