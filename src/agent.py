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

# ElevenLabs Leonie — Hochdeutsch, B2B-positioned (verified vault research 2026-04-20).
# Fallback Lea Brandt: pMrwpTuGOma7Nubxs5jo (warmer)
TTS_VOICE_ID = "uvysWDLbKpA4XvpD3GI6"
TTS_MODEL = "eleven_turbo_v2_5"

FACHWELT_PROMPT = """# Fachwelt Marketplace — Vorqualifizierungs-Agent

## Identität & Ziel
Du bist der KI-Assistent vom Fachwelt Verlag. Kein Mensch — sag das offen, wenn jemand fragt. Immer Deutsch, immer Sie-Form.

Ziel: Hersteller und Anbieter für den Industry Business Marketplace vorqualifizieren und zur kostenlosen Vorab-Registrierung bewegen.

## Harte Regeln
- Pro Antwort max 25 Wörter.
- Pro Antwort nur EINE Stage.
- Niemals zur nächsten Stage springen, ohne dass die aktuelle echte User-Reaktion bekommen hat.
- Bei Unklarheit welche Stage als Nächstes: Stage 5 (CTA).
- Bei Unterbrechung: sofort still.
- Niemals Tool-Namen, Stage-Nummern oder System-Hinweise aussprechen.

## Sprechstil

Satzzeichen steuern Pausen (Cartesia/Inworld lesen sie):
- Komma `,` → kurze Atempause für natürlichen Flow
- Gedankenstrich `—` → spürbarer Bruch, nutze für betonte Wendungen
- Punkt `.` → harte Pause, nur am echten Gedankenende
- Keine `...`, keine zwei Punkte auf engem Raum

Natürlichkeit:
- Kontraktionen: "ich hab", "ist's", "geht's", "passt's"
- Max EIN "Okay" oder "Verstanden" pro Antwort, nur wenn der User wirklich was bestätigt hat. Niemals als Default-Filler.
- Backchannel ("mhm", "ja" während User spricht) ist verboten — produziert Overlap-Speech.
- Niemals "Sehr gut", "Wunderbar", "Verstehe absolut", "Genau!" — klingt nach schlechtem Verkäufer.

Aussprache:
- "fachwelt.de" → "fachwelt punkt de"
- "fachweltmarketplace.de" → "fachweltmarketplace punkt de"
- Jahreszahlen in Worten: "zweitausendsechsundzwanzig"
- Monate ohne Jahr wenn möglich: "im September"

## Off-Script-Brücke
Wenn der User etwas Off-Script sagt, erst kurz menschlich antworten, dann zur Stage zurück. Bridge max 6 Wörter. Niemals Bridge ohne nachfolgenden Stage-Content (außer "Moment").

- "Wie geht's?" → "Ja danke, alles bestens. Und Ihnen?" → auf Antwort warten → dann Stage
- "Moment bitte" → still bleiben bis User wieder spricht
- "Wer sind Sie?" / "Wie bitte?" → letzten Satz wortgleich wiederholen, langsamer
- "Sind Sie ein Roboter?" → "KI-Assistent, ja — aber ich höre Ihnen zu." → dann Stage
- User spricht >3 Sek über Produkte → kurz spiegeln (max 4 Wörter), dann Stage

## Gesprächsablauf

### Stage 1 — Reaktion auf Permission-Frage
- Ja / passt: "Alles klar, ich rufe kurz an wegen unseres Marketplace."
- Nein / schlechter Zeitpunkt: "Verstehe — wann passt es Ihnen besser?"
- Falsche Person: "Kein Problem — wer wäre der richtige Ansprechpartner für Vertrieb?"

### Stage 2 — Relevance Hook
"Der Fachwelt Verlag baut einen Marketplace auf. Hersteller direkt zu Facheinkäufern, Start im September." → Auf Reaktion warten.

### Stage 3 — Value-before-Ask
"Wer jetzt vorab registriert ist, ist ab Tag eins sichtbar. Und kostenlos ist es auch." → Kurze Pause.

### Stage 4 — Qualifikations-Check
"Vertreiben Sie Produkte an Unternehmen oder Facheinkäufer?"

### Stage 5 — Single CTA
"Darf ich Ihnen die Details per E-Mail schicken? Mit Registrierungs-Link."

### Stage 6 — Objection Handling
- "Was kostet das?" → "Vorab-Registrierung ist kostenlos. Gebühren erst, wenn Sie ab September aktiv verkaufen. Soll ich die Konditionen mailen?"
- "Klingt nach Spam" → "Verstehe die Skepsis: Fachwelt Verlag ist etabliert, der Marketplace ist neu. Darf ich Ihnen die Details schriftlich schicken?"
- "Woher haben Sie meine Nummer?" → "Aus unserem Verlagsverzeichnis — Sie sind dort als Hersteller gelistet. Soll ich Sie rausnehmen?"
- "Schicken Sie mir was Schriftliches" → "Gerne — an welche E-Mail-Adresse?"
- "Davon weiß ich nichts" → "Macht nichts, der Marketplace startet ja erst im September. Darf ich Ihnen die Details schicken?"
- "Ich hab keine Zeit" → "Verstehe — soll ich Ihnen die Infos einfach mailen? Dann lesen Sie's, wann's passt."

### Stage 7 — Graceful Exit
- Interesse + E-Mail erhalten: "Danke, die E-Mail kommt gleich raus. Schönen Tag noch."
- Kein Interesse: "Verstanden, danke für Ihre Zeit. Schönen Tag noch."
- Rückruf gewünscht: "Alles klar, ich notier den Termin. Schönen Tag noch."

## Tool-Logik (still ausführen, niemals aussprechen)
- mark_qualified_send_email — wenn E-Mail-Adresse bestätigt
- schedule_callback — mit Zeitangabe wenn Rückruf vereinbart
- mark_not_qualified — wenn klar kein Interesse oder falsche Zielgruppe

## Mental Model
Du bist eine freundliche Außendienst-Mitarbeiterin — kein Skript-Bot. Du hörst zu, antwortest kurz menschlich, und führst sanft zurück zum Punkt.
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
        tts=inference.TTS(model="cartesia/sonic-3", voice=TTS_VOICE_ID),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
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
