import logging

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
    inference,
    room_io,
)
from livekit.plugins import ai_coustics, elevenlabs, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

AGENT_MODEL = "openai/gpt-4.1"

# Quality over latency — multilingual_v2 hat deutlich natürlichere DE-Prosodie als turbo
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Voice-Library Recherche (Fonio.ai nutzt ElevenLabs DE-Voices wie Katja, Julia, Theres)
# Johanna ist explizit als "Sales Outreach Specialist" gelabelt — exakt unser Use-Case
TTS_VOICE_ID = "HHKcxM1mAt4nEB2ZjrRw"  # Johanna - Sales Outreach Specialist (DE, conversational, confident)
# TTS_VOICE_ID = "cllvQaMvj0ZKxH88HGEn"  # Gesa Tess - Trustworthy Host (conversational, pleasant)
# TTS_VOICE_ID = "MGG5Irb57ATHvyIeTEYo"  # Maya - Supportive Agent (conversational, confident)
# TTS_VOICE_ID = "0o46iPcQNHBZFpnxxQz5"  # Marion Mitte - Friendly, Warm & Fresh
# TTS_VOICE_ID = "uvysWDLbKpA4XvpD3GI6"  # Leonie (alt)
# TTS_VOICE_ID = "sgKauqXbUxSBZgugAiOl"  # Sina - middle-aged, casual

# Reverse-engineered Phonio voice settings (vault research 2026-04-20)
PHONIO_VOICE_SETTINGS = elevenlabs.VoiceSettings(
    stability=0.45,
    similarity_boost=0.80,
    style=0.15,
    use_speaker_boost=True,
    speed=1.0,
)

# Pronunciation dictionary for German + English loanwords (Marketplace, B2B, KI, URL, fachwelt.de)
FACHWELT_PRONUNCIATION_DICT = [
    elevenlabs.PronunciationDictionaryLocator(
        pronunciation_dictionary_id="IW2jbKiFNq8Nz0X4kscp",
        version_id="4DMpzFxwGeOmuu6q7Knl",
    )
]

# Größere Chunks = fließendere Sprache, weniger Mini-Pausen zwischen Sätzen
PHONIO_CHUNK_SCHEDULE = [160, 250, 350]

FACHWELT_PROMPT = """# Fachwelt Marketplace — Outbound-Agent

## Wer du bist
Du bist Lisa, der KI-Assistent vom Fachwelt Verlag. Kein Mensch — wenn jemand fragt, sag's offen ("KI-Assistent, ja — aber ich hör Ihnen zu."). Immer Deutsch, immer Sie-Form. Du klingst wie eine erfahrene Außendienst-Mitarbeiterin: warm, direkt, nicht aufdringlich. Keine Verkäufer-Floskeln.

## Was du erreichen willst
Den Hersteller/Anbieter für den Fachwelt Marketplace vorqualifizieren und zur kostenlosen Vorab-Registrierung bewegen — idealerweise per E-Mail-Bestätigung am Telefon.

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
- **Fachwelt Verlag** — etablierter B2B-Fachverlag, baut gerade einen neuen Marketplace auf.
- **Marketplace-Modell**: Hersteller stellen Produkte ein, Facheinkäufer finden/anfragen direkt. Kein Zwischenhandel.
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

## Aussprache (kritisch)
- **"Marketplace"** → schreib's phonetisch als **"Marketpleis"** (englisch ausgesprochen, wie im Original). Niemals deutsch lesen lassen.
- **"fachwelt.de"** → "fachwelt punkt de"
- **"fachweltmarketplace.de"** → "fachwelt-marketpleis punkt de"
- **Jahreszahlen** ausgeschrieben: "zweitausendsechsundzwanzig"
- **Monate** ohne Jahr wenn möglich: "im September"
- E-Mail-Adressen Buchstabe für Buchstabe wiederholen zur Bestätigung.

## Einwände — Leitplanken, keine Skripte
- **"Was kostet das?"** → Vorab-Registrierung kostenlos, Gebühren erst beim aktiven Verkauf ab September. Konditionen gerne schriftlich.
- **"Klingt nach Spam"** → Skepsis verstehen, Verlag ist etabliert, Marketplace ist neu. Schriftlich nachreichen anbieten.
- **"Woher haben Sie meine Nummer?"** → Verlagsverzeichnis, er ist als Hersteller gelistet, kann auf Wunsch raus.
- **"Davon weiß ich nichts"** → Klar, startet ja erst September. Details mailen anbieten.
- **"Keine Zeit"** → Anbieten zu mailen, dann liest er's, wann's passt.
- **"Schicken Sie was Schriftliches"** → Sofort E-Mail-Adresse abfragen.

Formuliere immer frisch, nicht wortgleich. Hör zu, was *seine* Variante des Einwands ist, und antworte spezifisch.

## Tools (still ausführen, NIE aussprechen)
- `mark_qualified_send_email` — sobald eine E-Mail-Adresse bestätigt wurde (vorgelesen + bestätigt).
- `schedule_callback` — wenn er einen konkreten Rückruf-Zeitpunkt nennt.
- `mark_not_qualified` — bei klarem Nein, falscher Zielgruppe (kein B2B), falsche Person ohne Weiterleitung.

Nach Tool-Aufruf: kurzer freundlicher Abschied ("Danke, die E-Mail kommt raus. Schönen Tag noch.") und Schluss.

## Wenn du unsicher bist, was als Nächstes
Frag: "Darf ich Ihnen die Details einfach per E-Mail schicken?" — das ist der sichere Default. Aber nur, wenn's organisch passt, nicht als Reflex.
"""


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
        logger.info(f"[QUALIFIED] email={email}")
        # TODO: POST to webhook (n8n) for email send + CRM update
        return "Email queued. Continue to graceful exit."

    @function_tool
    async def schedule_callback(self, context: RunContext, when: str, notes: str = ""):
        """Schedule a callback when the user requests one.

        Args:
            when: Caller-provided time hint (e.g. "morgen Nachmittag", "nächste Woche Dienstag")
            notes: Optional additional context from the conversation
        """
        logger.info(f"[CALLBACK] when={when} notes={notes}")
        # TODO: POST to webhook for calendar/CRM
        return "Callback noted. Continue to graceful exit."

    @function_tool
    async def mark_not_qualified(self, context: RunContext, reason: str):
        """Mark the contact as not qualified and end gracefully.

        Args:
            reason: Short reason (e.g. "kein B2B", "kein Interesse", "falsche Person")
        """
        logger.info(f"[NOT_QUALIFIED] reason={reason}")
        # TODO: POST to webhook for CRM
        return "Marked not qualified. Continue to graceful exit."


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def fachwelt_agent(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="de"),
        llm=inference.LLM(model=AGENT_MODEL),
        tts=elevenlabs.TTS(
            voice_id=TTS_VOICE_ID,
            model=ELEVENLABS_MODEL,
            voice_settings=PHONIO_VOICE_SETTINGS,
            language="de",
            auto_mode=False,
            chunk_length_schedule=PHONIO_CHUNK_SCHEDULE,
            pronunciation_dictionary_locators=FACHWELT_PRONUNCIATION_DICT,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
        min_endpointing_delay=0.5,
        max_endpointing_delay=4.0,
        allow_interruptions=True,
        min_interruption_duration=0.4,
    )

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

    # Seed the conversation — opener that triggers Stage 1 reaction
    await session.say(
        "Guten Tag, hier ist Lisa vom Fachwelt Verlag. Hätten Sie kurz zwei Minuten?"
    )


if __name__ == "__main__":
    cli.run_app(server)
