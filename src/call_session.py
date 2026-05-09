"""Per-Call lifecycle orchestrator.

A `CallSession` owns one Call from `session.start` through shutdown:

  - wires the LiveKit listeners (errors, user input, conversation items)
  - plays the opener (pre-rendered MP3 for default text, live TTS otherwise)
  - starts the watchdog after the opener completes
  - spawns the silence watcher with the right teardown ordering
  - registers the shutdown callback that cancels both, finalizes the
    summary, and emits the call_summary log line

The single public method is `run()`. The caller (the worker `rtc_session`
entrypoint) is reduced to: load config, build session/assistant, build
sink, and `await CallSession(...).run()`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from livekit.agents import Agent, AgentSession, JobContext, room_io

from call_event_sink import (
    AgentTurn,
    CallEventSink,
    EntrypointException,
    OpenerPreflightFailed,
    SessionError,
    SilenceHangup,
    SilenceReprompt,
    UserTurnFinal,
)
from config_loader import AgentRuntimeConfig
from observability import CallSummary
from opener import OPENER_TEXT, opener_audio_frames, validate_opener_audio
from watchdog import CallWatchdog

# C13 — if the user is silent for this long after the opener finishes, prompt
# them once. After SILENCE_HANGUP_THRESHOLD_S more, give up and mark a callback.
# Callers occasionally drop silently (no SIP BYE) — without this watchdog the
# agent waits forever and the worker job is wasted.
SILENCE_REPROMPT_THRESHOLD_S = 15.0
SILENCE_HANGUP_THRESHOLD_S = 15.0
SILENCE_POLL_S = 0.5


class CallSession:
    def __init__(
        self,
        ctx: JobContext,
        session: AgentSession,
        assistant: Agent,
        config: AgentRuntimeConfig,
        sink: CallEventSink,
        summary: CallSummary,
        room_options: room_io.RoomOptions,
        on_finalize: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._ctx = ctx
        self._session = session
        self._assistant = assistant
        self._config = config
        self._sink = sink
        self._summary = summary
        self._room_options = room_options
        self._watchdog = CallWatchdog(session=session, sink=sink)
        self._silence_task: asyncio.Task[None] | None = None
        self._user_engaged = asyncio.Event()
        self._on_finalize_extra = on_finalize

    async def run(self) -> None:
        self._wire_listeners()
        self._ctx.add_shutdown_callback(self._finalize)

        try:
            await self._session.start(
                agent=self._assistant,
                room=self._ctx.room,
                room_options=self._room_options,
            )
            await self._play_opener()
            # Watchdog scoped to mid-conversation TTS hangs. The pre-rendered
            # opener is ~12s of legitimate playback, which would false-positive
            # the speaking-stuck timer. Arm only after opener completes.
            self._watchdog.start()
            # Kick off silence watcher only after opener completes so the
            # threshold doesn't include opener playback time.
            self._silence_task = asyncio.create_task(
                self._silence_watch(), name="silence-watch"
            )
        except Exception as e:
            self._sink.emit(
                EntrypointException(error_type=type(e).__name__, error=str(e))
            )
            raise

    # ── listeners ────────────────────────────────────────────────────────────

    def _wire_listeners(self) -> None:
        sink = self._sink
        engaged = self._user_engaged

        def _on_session_error(ev) -> None:
            source = getattr(getattr(ev, "source", None), "label", "unknown")
            err = getattr(ev, "error", ev)
            sink.emit(SessionError(source=str(source), error=str(err)))

        def _on_user_input(ev) -> None:
            if getattr(ev, "is_final", True):
                sink.emit(UserTurnFinal())
                engaged.set()

        def _on_conversation_item(ev) -> None:
            item = getattr(ev, "item", None)
            if item is not None and getattr(item, "role", None) == "assistant":
                sink.emit(AgentTurn())

        self._session.on("error", _on_session_error)
        self._session.on("user_input_transcribed", _on_user_input)
        self._session.on("conversation_item_added", _on_conversation_item)

    # ── opener ───────────────────────────────────────────────────────────────

    async def _play_opener(self) -> None:
        opener_text = self._config.opener_text
        opener_is_default = opener_text.strip() == OPENER_TEXT.strip()

        opener_audio = None
        if opener_is_default:
            try:
                validate_opener_audio()
                opener_audio = opener_audio_frames()
            except Exception as e:
                self._sink.emit(
                    OpenerPreflightFailed(
                        error_type=type(e).__name__, error=str(e)
                    )
                )

        if opener_audio is not None:
            await self._session.say(
                opener_text, audio=opener_audio, allow_interruptions=False
            )
        else:
            # Live-TTS fallback. Slightly slower first byte and prosody may
            # differ, but the call still has audio.
            await self._session.say(opener_text, allow_interruptions=False)

    # ── silence watcher ──────────────────────────────────────────────────────

    async def _silence_watch(self) -> None:
        """C13 — re-prompt once on real silence, hang up if user never engages.

        `user_engaged_event` is set by the `user_input_transcribed` listener
        as soon as STT produces any final transcript. Event-driven instead
        of polling session.user_state, which could miss short utterances
        ("ja", "mhm") that complete between polls.
        """
        last_activity = time.time()
        reprompted = False

        try:
            while True:
                await asyncio.sleep(SILENCE_POLL_S)
                now = time.time()

                if self._user_engaged.is_set():
                    return  # conversation flow takes over
                if self._session.agent_state == "speaking":
                    last_activity = now
                    continue

                idle = now - last_activity

                if not reprompted:
                    if idle < SILENCE_REPROMPT_THRESHOLD_S:
                        continue
                    self._sink.emit(SilenceReprompt())
                    await self._session.say(
                        self._config.silence_reprompt_text,
                        allow_interruptions=True,
                    )
                    reprompted = True
                    last_activity = time.time()
                    continue

                if idle < SILENCE_HANGUP_THRESHOLD_S:
                    continue
                if self._user_engaged.is_set():
                    return
                self._sink.emit(SilenceHangup(reason="no_response_after_opener"))
                await self._session.aclose()
                return
        except asyncio.CancelledError:
            pass
        except RuntimeError as e:
            # Race: shutdown started while we were past the cancel point.
            # AgentSession already closing — nothing to do.
            if "isn't running" not in str(e):
                raise

    # ── finalize ─────────────────────────────────────────────────────────────

    async def _finalize(self) -> None:
        # Cancel silence_task FIRST so it doesn't race the watchdog stop or
        # mutate summary mid-emit.
        if self._silence_task is not None and not self._silence_task.done():
            self._silence_task.cancel()
        self._watchdog.stop()
        if not self._summary.final_state or self._summary.final_state == "unknown":
            self._summary.final_state = "user_hangup"
        self._summary.emit()
        if self._on_finalize_extra is not None:
            await self._on_finalize_extra()
