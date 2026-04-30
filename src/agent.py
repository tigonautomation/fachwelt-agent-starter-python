import asyncio
import logging
import os
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator

import av
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    room_io,
)
from livekit.plugins import ai_coustics, deepgram, elevenlabs, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from health import start_health_server
from observability import CallSummary, fire_webhook, log_event, new_call_id
from watchdog import CallWatchdog

# F29 — flush stdout per record so Coolify/k8s log drivers receive partial-line
# output without buffering. PYTHONUNBUFFERED=1 in the Dockerfile is the
# belt-and-suspenders backup.
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Per-call context shared by the FachweltAssistant tools so they can emit
# structured events tagged with the right call_id without threading the
# context through every function signature.
_current_call: ContextVar[tuple[str, CallSummary] | None] = ContextVar(
    "_current_call", default=None
)
_active_sessions: int = 0
_health_runner = None

OPENER_AUDIO_PATH = Path(__file__).resolve().parent.parent / "assets" / "opener.mp3"
OPENER_TEXT = (
    "Guten Tag, hier ist Lisa vom Fachwelt Verlag. "
    "Kurzer Hinweis: ich bin eine KI, das Gespräch wird "
    "zur Qualitätssicherung aufgezeichnet. Wir bauen einen "
    "Marktplatz für B2B-Hersteller — hätten Sie zwei Minuten?"
)
OPENER_SAMPLE_RATE = 24000


async def opener_audio_frames() -> AsyncIterator[rtc.AudioFrame]:
    """Decode the pre-rendered opener MP3 and yield rtc.AudioFrame chunks.

    Bypasses live ElevenLabs TTS for the opener so every call sounds 100% identical
    (no sampling variance). Dynamic replies still use live TTS.
    """
    if not OPENER_AUDIO_PATH.exists():
        raise FileNotFoundError(f"Opener audio missing at {OPENER_AUDIO_PATH}")

    container = av.open(str(OPENER_AUDIO_PATH))
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=OPENER_SAMPLE_RATE)
        for packet in container.demux(stream):
            for frame in packet.decode():
                for resampled in resampler.resample(frame):
                    pcm = resampled.to_ndarray()
                    samples_per_channel = pcm.shape[-1]
                    yield rtc.AudioFrame(
                        data=pcm.astype("int16").tobytes(),
                        sample_rate=OPENER_SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=samples_per_channel,
                    )
        for resampled in resampler.resample(None) or []:
            pcm = resampled.to_ndarray()
            samples_per_channel = pcm.shape[-1]
            yield rtc.AudioFrame(
                data=pcm.astype("int16").tobytes(),
                sample_rate=OPENER_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=samples_per_channel,
            )
    finally:
        container.close()


AGENT_MODEL = "gpt-4.1"


def _build_llm() -> openai.LLM:
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        return openai.LLM.with_azure(
            model=AGENT_MODEL,
            azure_endpoint=azure_endpoint,
            azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
    return openai.LLM(model=AGENT_MODEL)

# Quality over latency — multilingual_v2 hat deutlich natürlichere DE-Prosodie als turbo
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Voice-Library Recherche (Fonio.ai nutzt ElevenLabs DE-Voices wie Katja, Julia, Theres)
TTS_VOICE_ID = "v3V1d2rk6528UrLKRuy8"  # Susi - Effortless and Confident (Voice-Scout 2026-04-29)

# Voice-Scout 2026-04-29 Setting B — Susi tied at 95.6% Levenshtein, won ear-test.
# Live-Validation 2026-04-30: stability 0.55 → 0.80 (Konsistenz), style 0.15 → 0.30 (etwas wärmer/freundlicher)
PHONIO_VOICE_SETTINGS = elevenlabs.VoiceSettings(
    stability=0.85,
    similarity_boost=0.80,
    style=0.20,
    use_speaker_boost=True,
    speed=0.95,
)

# Pronunciation dictionary for German + English loanwords (Marketplace, B2B, KI, URL, fachwelt.de)
FACHWELT_PRONUNCIATION_DICT = [
    elevenlabs.PronunciationDictionaryLocator(
        pronunciation_dictionary_id="0o1LBeebsgd55OeNqno8",
        version_id="911wJfN4YWGPhIuW9zvS",
    )
]

# Größere Chunks = fließendere Sprache, weniger Mini-Pausen zwischen Sätzen
PHONIO_CHUNK_SCHEDULE = [160, 250, 350]

FACHWELT_PROMPT = """# Fachwelt Marktplatz — Outbound-Agent

## Wer du bist
Du bist Lisa, der KI-Assistent vom Fachwelt Verlag. Kein Mensch — wenn jemand fragt ("Bist du ein Mensch?", "Ein Bot?", "Eine KI?"), bestätige IMMER offen ("Ja, ich bin ein KI-Assistent vom Fachwelt Verlag — aber ich hör Ihnen zu."), auch wenn du es im ersten Turn schon erwähnt hast. Niemals ausweichen, niemals verneinen. Immer Deutsch, immer Sie-Form. Du klingst wie eine erfahrene Außendienst-Mitarbeiterin: warm, direkt, nicht aufdringlich. Keine Verkäufer-Floskeln.

## Was du erreichen willst
Den Hersteller/Anbieter für den Fachwelt Marktplatz vorqualifizieren und zur kostenlosen Vorab-Registrierung bewegen — idealerweise per E-Mail-Bestätigung am Telefon.

Eine grobe Trajektorie hilft, ist aber kein Drehbuch:
1. Permission abklären (passt's gerade?)
2. **KI- und Aufzeichnungs-Disclosure** — sobald der User auf den Opener reagiert hat, knapp und natürlich: "Kurzer Hinweis vorab: ich bin eine KI vom Fachwelt Verlag, das Gespräch wird zur Qualitätssicherung aufgezeichnet." Nur einmal, nicht wiederholen.
3. Worum's geht in einem Satz
4. Relevanz/Wert für ihn
5. Ist er überhaupt Zielgruppe (B2B-Vertrieb)?
6. E-Mail einsammeln, Details schriftlich schicken
7. Sauber verabschieden

Folge der Reaktion des Users, nicht der Liste. Springe Schritte, wenn er schon Bescheid weiß. Wiederhole, wenn er unsicher ist. Wenn er offensichtlich kein Fit ist (kein B2B, falsche Person), brich freundlich ab.

## Was du über Fachwelt weißt
- **Fachwelt Verlag** — etablierter B2B-Fachverlag, baut gerade einen neuen Marktplatz auf.
- **Marktplatz-Modell**: Hersteller stellen Produkte ein, Facheinkäufer finden/anfragen direkt. Kein Zwischenhandel.
- **Launch**: September zweitausendsechsundzwanzig.
- **Vorab-Registrierung**: kostenlos, sichert Sichtbarkeit ab Tag eins.
- **Gebühren**: Erst wenn aktiv verkauft wird, ab September. Konditionen schickt das Team schriftlich.
- **Quelle der Nummer**: Verlagsverzeichnis — der Angerufene ist als Hersteller gelistet. Auf Wunsch rausnehmbar.
- **Zielgruppe**: Hersteller und Anbieter, die an Unternehmen/Facheinkäufer verkaufen. Endkunden-Shops sind nicht der Fit.

Wenn etwas Spezifisches gefragt wird, das du nicht weißt: ehrlich sagen ("Das hab ich nicht parat — soll's das Team Ihnen schriftlich schicken?"). Niemals erfinden.

## Sprechstil — strikt einhalten
- **Kurz**. Eine Antwort = max 1-2 Sätze, ~20 Wörter. Keine Aufzählungen am Telefon.
- **Eine Idee pro Antwort**. Nicht Wert + Frage + Termin + E-Mail in einem Atemzug.
- **Pausen via Satzzeichen**: Komma `,` für Atempause, Gedankenstrich `—` für betonten Bruch, Punkt `.` nur am echten Gedankenende. Keine `...`.
- **Kontraktionen**: "ich hab", "ist's", "geht's", "passt's", "wär".
- **Verboten**: "Sehr gut", "Wunderbar", "Genau!", "Verstehe absolut", "Perfekt!" — klingt nach schlechtem Verkäufer. Backchannel ("mhm", "ja") während User redet ist tabu (Overlap).
- **"Okay"/"Verstanden"** maximal einmal pro Antwort, nur wenn er wirklich etwas bestätigt hat. Nie als Filler-Auftakt.
- **Bei Unterbrechung**: sofort still.
- **Bei "Moment bitte"**: still bleiben, bis er weiterspricht.
- **Bei "Wie bitte?"/"Wer sind Sie?"**: letzten Satz wortgleich, etwas langsamer wiederholen.

## Aussprache (kritisch — strikt einhalten)
- **"Marketplace" — NIEMALS so schreiben oder sagen.** Schreib IMMER **"Marktplatz"** (deutsch). Beispiele:
  - ❌ "unserem Marketplace" → ✅ "unserem Marktplatz"
  - ❌ "der Fachwelt-Marketplace" → ✅ "der Fachwelt-Marktplatz"
  - ❌ "auf dem Marketplace" → ✅ "auf dem Marktplatz"
  - Diese Regel gilt für JEDE Erwähnung — auch in Aufzählungen, Nebensätzen, Wiederholungen.
- **"fachwelt.de"** → "fachwelt punkt de"
- **"fachweltmarketplace.de"** → "fachwelt-marktplatz punkt de"
- **Jahreszahlen** ausgeschrieben: "zweitausendsechsundzwanzig"
- **Monate** ohne Jahr wenn möglich: "im September"

## E-Mail-Adresse einsammeln (zwei Schritte, NIE überspringen)
1. **Erst wiederholen**: Sobald der User eine E-Mail nennt, lies sie zurück — Vor-Punkt-Teil und Nach-Punkt-Teil getrennt, in klar verständlicher Form ("max punkt mustermann at firma minus beispiel punkt de"). Frag dann: "Stimmt das so?"
2. **Erst nach Bestätigung Tool aufrufen**: `mark_qualified_send_email` rufst du **erst** auf, wenn der User die Wiederholung bestätigt hat. Niemals davor.

Wenn der User korrigiert: Wiederholung mit Korrektur, neu fragen. Wenn er beim ersten Mal explizit bestätigt ("ja, genau, korrekt"), ein zweites Wiederholen ist unnötig.

## Einwände — Leitplanken, keine Skripte
- **"Was kostet das?"** → Vorab-Registrierung kostenlos, Gebühren erst beim aktiven Verkauf ab September. Konditionen gerne schriftlich.
- **"Klingt nach Spam"** → Skepsis verstehen, Verlag ist etabliert, Marktplatz ist neu. Schriftlich nachreichen anbieten.
- **"Woher haben Sie meine Nummer?"** → Verlagsverzeichnis, er ist als Hersteller gelistet, kann auf Wunsch raus.
- **"Davon weiß ich nichts"** → Klar, startet ja erst September. Details mailen anbieten.
- **"Keine Zeit"** → Anbieten zu mailen, dann liest er's, wann's passt.
- **"Schicken Sie was Schriftliches"** → Sofort E-Mail-Adresse abfragen.

Formuliere immer frisch, nicht wortgleich. Hör zu, was *seine* Variante des Einwands ist, und antworte spezifisch.

## Tools (still ausführen, NIE aussprechen)

Du hast genau drei Tools. **Bevor** du den letzten verbalen Satz vor dem Abschied sprichst, prüf diese Checklist und ruf das passende Tool **zuerst**:

| User-Signal | Tool | reason/email/when |
|---|---|---|
| User bestätigt seine E-Mail-Adresse | `mark_qualified_send_email` | `email=<bestätigte Adresse>` |
| User nennt Rückruf-Wunsch (auch vage: "morgen Vormittag") | `schedule_callback` | `when=<O-Ton>`, `notes=<Anlass>` |
| "kein Interesse" / "nein danke" / "passt nicht" / "nervt" / Frust | `mark_not_qualified` | `reason="kein Interesse"` |
| Reines B2C, kein B2B-Fit | `mark_not_qualified` | `reason="kein B2B-Fit"` |
| Falsche Person ohne Weiterleitung möglich | `mark_not_qualified` | `reason="falsche Person"` |
| Privatperson irrtümlich im Verzeichnis | `mark_not_qualified` | `reason="kein Hersteller"` |
| Unmögliche Forderungen die du nicht zusagen kannst | `mark_not_qualified` | `reason="unmögliche Forderung"` |

**Verbaler Abschied ZUERST, dann Tool-Call — IMMER in dieser Reihenfolge.** Sprich erst den expliziten Grund + Abschied aus (z.B. bei `mark_not_qualified`: "Verstehe, das passt dann nicht — vielen Dank für Ihre Zeit, einen schönen Tag noch." / bei `mark_qualified_send_email`: "Perfekt, die E-Mail kommt raus. Schönen Tag noch." / bei `schedule_callback`: "Alles klar, ich melde mich dann. Einen guten Tag."), DANACH ruf das Tool. Niemals Tool ohne vorherigen verbalen Abschied — sonst wirkt's abgehackt und der User hört Stille. Auch wenn du Apologie ("tut mir leid"), Nummer-Opt-Out, oder schriftlichen Versand mit anbietest, das Tool wird trotzdem gerufen. Ein "kein-Interesse"-Anrufer verlässt das Gespräch immer mit `mark_not_qualified` — kein Pardon.

## Wenn du unsicher bist, was als Nächstes
Frag: "Darf ich Ihnen die Details einfach per E-Mail schicken?" — das ist der sichere Default. Aber nur, wenn's organisch passt, nicht als Reflex.
"""


def _record_tool_outcome(state: str, reason: str, **fields: object) -> None:
    """Stamp the call summary + emit a structured event + fire the CRM webhook.

    Tools must return immediately so the agent can speak the goodbye line, so
    the webhook is dispatched as a background task. The summary record makes
    the per-call disposition observable even if the webhook is spooled.
    """
    ctx = _current_call.get()
    if ctx is None:
        # Tests instantiate the agent without an rtc_session — no call context.
        logger.info(f"[{state.upper()}] reason={reason} fields={fields}")
        return
    call_id, summary = ctx
    summary.final_state = state
    summary.final_reason = reason
    log_event(call_id, f"tool_{state}", reason=reason, **fields)
    fire_webhook(call_id, state, {"reason": reason, **fields}, summary=summary)


class FachweltAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=FACHWELT_PROMPT)

    @function_tool
    async def mark_qualified_send_email(self, context: RunContext, email: str):
        """Mark the contact as qualified and queue the registration email.

        Call this ONLY when the user has explicitly confirmed an email address.

        Args:
            email: The confirmed email address (e.g. max@firma.de)
        """
        _record_tool_outcome("qualified", reason="email_confirmed", email=email)
        return "Email queued. Continue to graceful exit."

    @function_tool
    async def schedule_callback(self, context: RunContext, when: str, notes: str = ""):
        """Schedule a callback when the user requests one.

        Args:
            when: Caller-provided time hint (e.g. "morgen Nachmittag", "nächste Woche Dienstag")
            notes: Optional additional context from the conversation
        """
        _record_tool_outcome("callback", reason=when, notes=notes)
        return "Callback noted. Continue to graceful exit."

    @function_tool
    async def mark_not_qualified(self, context: RunContext, reason: str):
        """Mark the contact as not qualified and end gracefully.

        Args:
            reason: Short reason (e.g. "kein B2B", "kein Interesse", "falsche Person")
        """
        _record_tool_outcome("not_qualified", reason=reason)
        return "Marked not qualified. Continue to graceful exit."


# Hard cap on simultaneous calls. Edon's outbound dialer realistically queues
# 2-3 conversations at peak; 5 leaves headroom without exposing us to a
# runaway pool that would burn API quota or starve LK Cloud capacity.
# When `_active_sessions` hits this number, `_load_fn` returns 1.0 and LK
# Agents stops accepting new jobs (the default load_threshold is 0.7).
MAX_CONCURRENT_SESSIONS = 5


def _load_fn() -> float:
    return min(_active_sessions / MAX_CONCURRENT_SESSIONS, 1.0)


server = AgentServer(
    num_idle_processes=MAX_CONCURRENT_SESSIONS,
    load_fnc=_load_fn,
)


# C13 — if the user is silent for this long after the opener finishes, prompt
# them once. After SILENCE_HANGUP_THRESHOLD_S more, give up and mark a callback.
# Callers occasionally drop silently (no SIP BYE) — without this watchdog the
# agent waits forever and the worker job is wasted.
SILENCE_REPROMPT_THRESHOLD_S = 15.0
SILENCE_HANGUP_THRESHOLD_S = 15.0
SILENCE_REPROMPT_TEXT = (
    "Sind Sie noch dran? Falls die Verbindung schlecht ist, "
    "rufe ich gerne nochmal an."
)


def prewarm(proc: JobProcess):
    """Per-process warmup. Loads the VAD model so the first call doesn't pay for it."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


async def _silence_watch(session: AgentSession, call_id: str, summary: CallSummary) -> None:
    """C13 — re-prompt once if the user never responds, then bail with a callback.

    We only consider "silent" when the agent is idle/listening AND the user is
    not currently speaking. If the user does speak, the regular conversation
    flow runs and this task naturally never fires.
    """
    try:
        await asyncio.sleep(SILENCE_REPROMPT_THRESHOLD_S)
        if session.user_state == "speaking" or session.agent_state == "speaking":
            return
        log_event(call_id, "silence_reprompt_after_opener")
        await session.say(SILENCE_REPROMPT_TEXT, allow_interruptions=True)
        await asyncio.sleep(SILENCE_HANGUP_THRESHOLD_S)
        if session.user_state == "speaking" or session.agent_state == "speaking":
            return
        log_event(call_id, "silence_hangup_no_response")
        summary.final_state = "callback"
        summary.final_reason = "no_response_after_opener"
        fire_webhook(
            call_id,
            "callback",
            {"reason": "no_response_after_opener"},
            summary=summary,
        )
        await session.aclose()
    except asyncio.CancelledError:
        pass


@server.rtc_session()
async def fachwelt_agent(ctx: JobContext):
    """Per-call entrypoint. Wires call_id, watchdog, silence-watch, and cleanup."""
    global _active_sessions, _health_runner

    if _health_runner is None:
        try:
            _health_runner = await start_health_server(lambda: _active_sessions)
        except OSError as e:
            # Port already bound by a sibling JobProcess in the same worker.
            # Log + carry on; one server per worker is enough for liveness probes.
            logger.warning(f"health server bind skipped: {e}")
            _health_runner = "shared"  # sentinel to skip future attempts

    call_id = new_call_id(ctx.room.name)
    ctx.log_context_fields = {"room": ctx.room.name, "call_id": call_id}
    summary = CallSummary(call_id=call_id, room=ctx.room.name)
    _current_call.set((call_id, summary))
    _active_sessions += 1
    log_event(call_id, "call_started", room=ctx.room.name)

    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="de",
            base_url="https://api.eu.deepgram.com/v1/listen",
        ),
        llm=_build_llm(),
        tts=elevenlabs.TTS(
            voice_id=TTS_VOICE_ID,
            model=ELEVENLABS_MODEL,
            voice_settings=PHONIO_VOICE_SETTINGS,
            language="de",
            auto_mode=False,
            chunk_length_schedule=PHONIO_CHUNK_SCHEDULE,
            # pronunciation_dictionary_locators=FACHWELT_PRONUNCIATION_DICT,  # disabled — testing if aliases hurt prosody
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
        min_endpointing_delay=0.2,
        max_endpointing_delay=1.5,
        allow_interruptions=True,
        min_interruption_duration=1.0,
    )

    # D21 — capture stream errors as structured events so the call summary
    # can attribute drops to a specific subsystem (TTS / STT / LLM).
    def _on_session_error(ev) -> None:  # noqa: ANN001 — LK ev type changes between minors
        source = getattr(getattr(ev, "source", None), "label", "unknown")
        err = getattr(ev, "error", ev)
        summary.record_error(source=str(source), error=str(err))
        log_event(call_id, "session_error", source=str(source), error=str(err))

    def _on_user_input(ev) -> None:  # noqa: ANN001
        if getattr(ev, "is_final", True):
            summary.user_turns += 1

    def _on_conversation_item(ev) -> None:  # noqa: ANN001
        item = getattr(ev, "item", None)
        if item is not None and getattr(item, "role", None) == "assistant":
            summary.agent_turns += 1

    session.on("error", _on_session_error)
    session.on("user_input_transcribed", _on_user_input)
    session.on("conversation_item_added", _on_conversation_item)

    watchdog = CallWatchdog(session=session, call_id=call_id, summary=summary)
    silence_task: asyncio.Task[None] | None = None

    async def _finalize() -> None:
        global _active_sessions
        if silence_task is not None and not silence_task.done():
            silence_task.cancel()
        watchdog.stop()
        if not summary.final_state or summary.final_state == "unknown":
            summary.final_state = "user_hangup"
        summary.emit()
        _active_sessions = max(0, _active_sessions - 1)

    ctx.add_shutdown_callback(_finalize)

    try:
        await session.start(
            agent=FachweltAssistant(),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=ai_coustics.audio_enhancement(
                        model=ai_coustics.EnhancerModel.QUAIL_VF_L
                    ),
                ),
            ),
        )

        await ctx.connect()

        # Seed the conversation — opener as pre-rendered audio for 100% consistency.
        # Pre-flight validate the MP3 because LK Agents swallows audio-iterator
        # exceptions via @log_exceptions; a corrupted/missing file would silently
        # produce zero audio and the user hears "Hallo? Hallo?". (A3 silent-fail fix)
        opener_audio = None
        try:
            if not OPENER_AUDIO_PATH.exists():
                raise FileNotFoundError(f"opener missing at {OPENER_AUDIO_PATH}")
            _validate = av.open(str(OPENER_AUDIO_PATH))
            _validate.close()
            opener_audio = opener_audio_frames()
        except Exception as e:
            log_event(
                call_id,
                "opener_audio_preflight_failed",
                error_type=type(e).__name__,
                error=str(e),
            )
            summary.record_error(source="opener_audio", error=str(e))

        if opener_audio is not None:
            await session.say(OPENER_TEXT, audio=opener_audio, allow_interruptions=False)
        else:
            # Live-TTS fallback. Slightly slower first byte and prosody may differ,
            # but the call still has audio.
            await session.say(OPENER_TEXT, allow_interruptions=False)

        # Watchdog scoped to mid-conversation TTS hangs. The pre-rendered opener
        # is 12.4s of legitimate playback, which would false-positive the 10s
        # speaking-stuck timer. Arm only after opener completes.
        watchdog.start()

        # C13 — kick off silence watcher only after opener completes so
        # the threshold doesn't include opener playback time.
        silence_task = asyncio.create_task(
            _silence_watch(session, call_id, summary), name="silence-watch"
        )
    except Exception as e:
        summary.record_error(source="entrypoint", error=str(e))
        log_event(call_id, "entrypoint_exception", error=str(e), error_type=type(e).__name__)
        raise


if __name__ == "__main__":
    cli.run_app(server)
