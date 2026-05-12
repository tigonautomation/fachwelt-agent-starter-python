import asyncio
import logging
import os
import sys
import time

from dotenv import load_dotenv
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
from livekit.rtc import ParticipantKind

from call_event_sink import CallEventSink, ToolInvoked, production_sink
from call_session import CallSession
from config_loader import AgentRuntimeConfig, load_runtime_config
from health import start_health_server
from observability import CallSummary, log_event, new_call_id
from opener import (
    OPENER_AUDIO_PATH,
    OPENER_SAMPLE_RATE,
    OPENER_TEXT,
    opener_audio_frames,
)

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

_active_sessions: int = 0
_health_runner = None


class _NullSink:
    """Default sink for tests that construct FachweltAssistant without a session."""

    def emit(self, event: object) -> None:
        return None


# Re-exports kept for test imports (`from agent import OPENER_AUDIO_PATH, ...`).
__all__ = [
    "FACHWELT_PROMPT",
    "OPENER_AUDIO_PATH",
    "OPENER_SAMPLE_RATE",
    "OPENER_TEXT",
    "FachweltAssistant",
    "opener_audio_frames",
]


AGENT_MODEL = "gpt-4.1"


def _build_llm(temperature: float | None = None) -> openai.LLM:
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        kwargs = dict(
            model=AGENT_MODEL,
            azure_endpoint=azure_endpoint,
            azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
        if temperature is not None:
            kwargs["temperature"] = temperature
        return openai.LLM.with_azure(**kwargs)
    if temperature is not None:
        return openai.LLM(model=AGENT_MODEL, temperature=temperature)
    return openai.LLM(model=AGENT_MODEL)

# Quality over latency — multilingual_v2 hat deutlich natürlichere DE-Prosodie als turbo
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Voice-Library Recherche (Fonio.ai nutzt ElevenLabs DE-Voices wie Katja, Julia, Theres)
TTS_VOICE_ID = "v3V1d2rk6528UrLKRuy8"  # Susi - Effortless and Confident (Voice-Scout 2026-04-29)

# Voice-Scout 2026-04-29 Setting B — Susi tied at 95.6% Levenshtein, won ear-test.
# Live-Validation 2026-04-30: stability 0.55 → 0.80 (Konsistenz), style 0.15 → 0.30 (etwas wärmer/freundlicher)
DEFAULT_VOICE_SPEED = 0.95


def _voice_settings(speed: float = DEFAULT_VOICE_SPEED) -> elevenlabs.VoiceSettings:
    return elevenlabs.VoiceSettings(
        stability=0.95,
        similarity_boost=0.80,
        style=0.20,
        use_speaker_boost=True,
        speed=speed,
    )


PHONIO_VOICE_SETTINGS = _voice_settings()

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
| User nennt Rückruf-Wunsch zu späterem Zeitpunkt ("morgen Vormittag", "später", "nächste Woche") | `schedule_callback` | `when=<O-Ton>`, `notes=<Anlass>`, `requested_human=False` |
| User verlangt explizit Rückruf von einem Menschen ("von einem Menschen", "echte Person", "persönlich") | `schedule_callback` | `when=<O-Ton>`, `notes=<Anlass>`, `requested_human=True` |
| "kein Interesse" / "nein danke" / "passt nicht" / "nervt" / Frust | `mark_not_qualified` | `reason="kein Interesse"` |
| Reines B2C, kein B2B-Fit | `mark_not_qualified` | `reason="kein B2B-Fit"` |
| Falsche Person ohne Weiterleitung möglich | `mark_not_qualified` | `reason="falsche Person"` |
| Privatperson irrtümlich im Verzeichnis | `mark_not_qualified` | `reason="kein Hersteller"` |
| Unmögliche Forderungen die du nicht zusagen kannst | `mark_not_qualified` | `reason="unmögliche Forderung"` |

**Verbaler Abschied ZUERST, dann Tool-Call — IMMER in dieser Reihenfolge.** Sprich erst einen warmen, vollständigen Abschluss aus (zwei kurze Sätze, nicht abgehackt), DANACH ruf das Tool.

Beispiele für gute Abschlüsse:
- `mark_qualified_send_email`: "Wunderbar, dann schicke ich Ihnen die Details gleich per Mail. Vielen Dank für Ihre Zeit und einen schönen Tag noch, Herr/Frau [Name]." (Name nur wenn bekannt.)
- `mark_not_qualified`: "Alles klar, dann passt das im Moment nicht. Vielen Dank, dass Sie sich die Zeit genommen haben — einen schönen Tag noch."
- `schedule_callback`: "Verstehe, dann melde ich mich zum vereinbarten Zeitpunkt nochmal. Bis dahin einen schönen Tag, Herr/Frau [Name]."

Niemals Tool ohne vorherigen verbalen Abschied — sonst hört der User Stille. Vermeide kurze, abgehackte Phrasen wie "Mail kommt raus, schönen Tag" — das klingt mechanisch. Zwei Sätze, ruhig und freundlich. Auch wenn du Apologie ("tut mir leid"), Nummer-Opt-Out, oder schriftlichen Versand mit anbietest, das Tool wird trotzdem gerufen. Ein "kein-Interesse"-Anrufer verlässt das Gespräch immer mit `mark_not_qualified` — kein Pardon.

## Wenn du unsicher bist, was als Nächstes
Frag: "Darf ich Ihnen die Details einfach per E-Mail schicken?" — das ist der sichere Default. Aber nur, wenn's organisch passt, nicht als Reflex.
"""


class FachweltAssistant(Agent):
    def __init__(
        self,
        instructions: str | None = None,
        sink: CallEventSink | None = None,
    ) -> None:
        super().__init__(instructions=instructions or FACHWELT_PROMPT)
        self._sink: CallEventSink = sink or _NullSink()

    @function_tool
    async def mark_qualified_send_email(self, context: RunContext, email: str):
        """Mark the contact as qualified and queue the registration email.

        Call this ONLY when the user has explicitly confirmed an email address.

        Args:
            email: The confirmed email address (e.g. max@firma.de)
        """
        self._sink.emit(
            ToolInvoked(
                state="qualified",
                reason="email_confirmed",
                fields={"email": email},
            )
        )
        return "Email queued. Continue to graceful exit."

    @function_tool
    async def schedule_callback(
        self,
        context: RunContext,
        when: str,
        notes: str = "",
        requested_human: bool = False,
    ):
        """Schedule a callback when the user requests one.

        Args:
            when: Caller-provided time hint (e.g. "morgen Nachmittag", "nächste Woche Dienstag")
            notes: Optional additional context from the conversation
            requested_human: True only if the caller explicitly asks to be called
                back by a human ("von einem Menschen", "persönlich", "echte Person").
                False (default) for neutral "später zurückrufen" — those become
                AI re-call slots in the dashboard.
        """
        self._sink.emit(
            ToolInvoked(
                state="callback",
                reason=when,
                fields={"notes": notes, "requested_human": bool(requested_human)},
            )
        )
        return "Callback noted. Continue to graceful exit."

    @function_tool
    async def mark_not_qualified(self, context: RunContext, reason: str):
        """Mark the contact as not qualified and end gracefully.

        Args:
            reason: Short reason (e.g. "kein B2B", "kein Interesse", "falsche Person")
        """
        self._sink.emit(
            ToolInvoked(state="not_qualified", reason=reason, fields={})
        )
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


SILENCE_REPROMPT_TEXT = (
    "Sind Sie noch dran? Falls die Verbindung schlecht ist, "
    "rufe ich gerne nochmal an."
)


def prewarm(proc: JobProcess):
    """Per-process warmup. Loads the VAD model so the first call doesn't pay for it."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


def _build_session(runtime_cfg: AgentRuntimeConfig, ctx: JobContext) -> AgentSession:
    return AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="de",
            base_url="https://api.eu.deepgram.com/v1/listen",
        ),
        llm=_build_llm(temperature=runtime_cfg.temperature),
        tts=elevenlabs.TTS(
            voice_id=TTS_VOICE_ID,
            model=ELEVENLABS_MODEL,
            voice_settings=_voice_settings(speed=runtime_cfg.voice_speed),
            language="de",
            auto_mode=False,
            chunk_length_schedule=PHONIO_CHUNK_SCHEDULE,
            # pronunciation_dictionary_locators=FACHWELT_PRONUNCIATION_DICT,  # disabled — testing if aliases hurt prosody
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
        min_endpointing_delay=0.2,
        max_endpointing_delay=0.5,
        allow_interruptions=True,
        min_interruption_duration=1.0,
    )


def _build_room_options() -> room_io.RoomOptions:
    return room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_L
            ),
        ),
    )


@server.rtc_session()
async def fachwelt_agent(ctx: JobContext):
    """Per-call entrypoint. Loads config, builds session, hands off to CallSession."""
    global _active_sessions, _health_runner

    if _health_runner is None:
        try:
            _health_runner = await start_health_server(lambda: _active_sessions)
        except OSError as e:
            # Port already bound by a sibling JobProcess in the same worker.
            # Log + carry on; one server per worker is enough for liveness probes.
            logger.warning(f"health server bind skipped: {e}")
            _health_runner = "shared"  # sentinel to skip future attempts

    # Provisional id; replaced with dashboard-supplied voice_call_id once
    # room.metadata is loaded. This keeps very-early log lines attributable.
    call_id = new_call_id(ctx.room.name)
    ctx.log_context_fields = {"room": ctx.room.name, "call_id": call_id}
    _active_sessions += 1
    call_start_ts = time.time()
    log_event(call_id, "call_started", room=ctx.room.name)

    # Block 1 — gate opener on SIP `callStatus="active"` (= callee picked up),
    # NOT on participant_connected. The SIP participant joins the room at
    # dialing/ringing time, long before answer — using that as the signal
    # caused the agent to talk over ringback. The canonical answer-signal is
    # the `sip.callStatus` attribute flipping to `"active"`. Listener attached
    # BEFORE ctx.connect() to avoid the connect-vs-event race.
    caller_connected = asyncio.Event()
    caller_disconnected = asyncio.Event()

    def _check_sip_status(participant, source: str) -> None:
        if participant.kind != ParticipantKind.PARTICIPANT_KIND_SIP:
            return
        attrs = dict(participant.attributes or {})
        status = attrs.get("sip.callStatus")
        log_event(
            call_id,
            "sip_attrs_snapshot",
            source=source,
            identity=participant.identity,
            status=status,
            attrs=attrs,
        )
        if status == "active" and not caller_connected.is_set():
            caller_connected.set()
            log_event(call_id, "sip_call_active", identity=participant.identity)
        elif status == "hangup" and not caller_disconnected.is_set():
            caller_disconnected.set()
            log_event(call_id, "sip_call_hangup", identity=participant.identity)

    def _on_participant_attributes_changed(changed, participant) -> None:
        log_event(
            call_id,
            "participant_attrs_changed",
            identity=getattr(participant, "identity", "?"),
            kind=str(getattr(participant, "kind", "?")),
            changed=dict(changed or {}),
        )
        _check_sip_status(participant, source="attrs_changed")

    def _on_participant_connected(participant) -> None:
        log_event(
            call_id,
            "participant_connected_raw",
            identity=getattr(participant, "identity", "?"),
            kind=str(getattr(participant, "kind", "?")),
            attrs=dict(getattr(participant, "attributes", {}) or {}),
        )
        if participant.kind == ParticipantKind.PARTICIPANT_KIND_SIP:
            # Dashboard uses waitUntilAnswered=true on createSipParticipant, so
            # SIP participant joins room ONLY after answered. participant_connected
            # for SIP kind = guaranteed pickup. Set directly, keep callStatus check
            # as belt-and-suspenders.
            if not caller_connected.is_set():
                caller_connected.set()
                log_event(
                    call_id,
                    "sip_pickup_via_participant_connected",
                    identity=participant.identity,
                )
            _check_sip_status(participant, source="participant_connected")

    def _on_participant_disconnected(participant) -> None:
        log_event(
            call_id,
            "participant_disconnected_raw",
            identity=getattr(participant, "identity", "?"),
            kind=str(getattr(participant, "kind", "?")),
        )
        if participant.kind == ParticipantKind.PARTICIPANT_KIND_SIP:
            caller_disconnected.set()

    def _on_track_subscribed(track, pub, participant) -> None:
        log_event(
            call_id,
            "track_subscribed_raw",
            identity=getattr(participant, "identity", "?"),
            kind=str(getattr(participant, "kind", "?")),
            track_kind=str(getattr(track, "kind", "?")),
        )
        # Fallback pickup signal: first audio track from SIP participant = media flowing.
        if (
            participant.kind == ParticipantKind.PARTICIPANT_KIND_SIP
            and not caller_connected.is_set()
            and str(getattr(track, "kind", "")).lower().endswith("audio")
        ):
            caller_connected.set()
            log_event(
                call_id,
                "sip_pickup_via_track_subscribed",
                identity=participant.identity,
            )

    ctx.room.on("participant_attributes_changed", _on_participant_attributes_changed)
    ctx.room.on("participant_connected", _on_participant_connected)
    ctx.room.on("participant_disconnected", _on_participant_disconnected)
    ctx.room.on("track_subscribed", _on_track_subscribed)

    # Connect first so room.metadata (set by the dashboard token route) is
    # available before we build the session and assistant.
    await ctx.connect()

    # Catch the race where the SIP participant joined (and possibly already
    # flipped to active) between event-attach and connect: scan present
    # remote participants once after connect.
    for p in ctx.room.remote_participants.values():
        _on_participant_connected(p)

    raw_metadata = getattr(ctx.room, "metadata", None) or ""
    runtime_cfg = load_runtime_config(
        raw_metadata,
        default_system_prompt=FACHWELT_PROMPT,
        default_opener_text=OPENER_TEXT,
        default_silence_reprompt_text=SILENCE_REPROMPT_TEXT,
    )
    if runtime_cfg.voice_call_id:
        log_event(call_id, "call_id_rebound", new_call_id=runtime_cfg.voice_call_id)
        call_id = runtime_cfg.voice_call_id
        ctx.log_context_fields = {"room": ctx.room.name, "call_id": call_id}

    summary = CallSummary(call_id=call_id, room=ctx.room.name)
    sink = production_sink(call_id, summary)
    log_event(
        call_id,
        "runtime_config_loaded",
        config_id=runtime_cfg.config_id,
        config_name=runtime_cfg.name,
        temperature=runtime_cfg.temperature,
        voice_speed=runtime_cfg.voice_speed,
        max_call_duration_s=runtime_cfg.max_call_duration_s,
        voice_call_id_from_metadata=bool(runtime_cfg.voice_call_id),
    )

    async def _decrement_active() -> None:
        global _active_sessions
        _active_sessions = max(0, _active_sessions - 1)

    call = CallSession(
        ctx=ctx,
        session=_build_session(runtime_cfg, ctx),
        assistant=FachweltAssistant(
            instructions=runtime_cfg.system_prompt.text, sink=sink
        ),
        config=runtime_cfg,
        sink=sink,
        summary=summary,
        room_options=_build_room_options(),
        caller_connected=caller_connected,
        caller_disconnected=caller_disconnected,
        call_start_ts=call_start_ts,
        on_finalize=_decrement_active,
    )
    await call.run()


if __name__ == "__main__":
    cli.run_app(server)
