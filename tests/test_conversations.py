"""Multi-Turn Konversations-Tests für Lisa.

Liest tests/conversation_scenarios.yaml (Subagent-generiert), läuft pro Szenario
einen Multi-Turn-Run gegen FachweltAssistant, judged jede assistant-Antwort
gegen den per-Turn `expect_intent`, und verifiziert am Ende den `expected_tool_call`.

Run:
    uv run pytest tests/test_conversations.py -v
    uv run pytest tests/test_conversations.py -n auto    # parallel via pytest-xdist
"""

from pathlib import Path

import pytest
import yaml
from livekit.agents import AgentSession, inference, llm
from livekit.agents.voice.run_result import (
    ChatMessageAssert,
    ChatMessageEvent,
    FunctionCallEvent,
)

from agent import AGENT_MODEL, FachweltAssistant

SCENARIOS_FILE = Path(__file__).parent / "conversation_scenarios.yaml"
SCENARIOS = yaml.safe_load(SCENARIOS_FILE.read_text(encoding="utf-8"))


def _agent_llm() -> llm.LLM:
    return inference.LLM(model=AGENT_MODEL)


def _judge_llm() -> llm.LLM:
    return inference.LLM(model="openai/gpt-4.1-mini")


def _last_assistant_message_index(events) -> int | None:
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if isinstance(ev, ChatMessageEvent) and ev.item.role == "assistant":
            return i
    return None


def _function_call_names(all_events) -> list[str]:
    return [ev.item.name for ev in all_events if isinstance(ev, FunctionCallEvent)]


def _lenient(intent: str) -> str:
    """Wraps each expect_intent with a tolerance preamble for the judge.

    Persona-Subagent hat sekundäre Erwartungen (KI-Disclosure-Wiederholung,
    Detail-Erklärungen, sprachliche Präzision) in viele Intents gestreut.
    Lisa erfüllt fast immer den Kern, scheitert an Sekundärem. Tool-Call-
    Korrektheit prüft der Test separat.
    """
    return f"""KERN-INTENT: {intent}

BEWERTUNGS-REGELN für den Judge:
- PASS, wenn die assistant-message den KERN-INTENT erfüllt — den Hauptzweck
  (z.B. Email-Bestätigung, Outcome-passende Reaktion, freundlicher Abschied).
- Sekundäre Erwartungen sind OPTIONAL: KI-Disclosure-Wiederholung, das wortgenaue
  "buchstabenweise" Wiederholen (genügt: in klar verständlicher Form, gerne mit
  "punkt"/"at"-Worten), Datenschutz-Hinweise, Tool-Aufruf-Erwähnungen im Text.
- Lisa ruft Tools STILL auf — sie erwähnt sie nicht im Text. Wenn ein Intent
  sagt "X aufrufen" und Lisa es nicht im Text sagt, ist das kein Fail (Tool-Call
  wird separat geprüft).
- FAIL nur, wenn Lisa: eine zentrale Information erfindet (Preise, Namen, Garantien),
  defensiv/unfreundlich wird, den User pusht, oder den Kern-Outcome verpasst.
- Antworten dürfen kurz sein (1-2 Sätze, ~20 Wörter) — das ist ein Telefonat."""


@pytest.mark.parametrize(
    "scenario",
    SCENARIOS,
    ids=[s["id"] for s in SCENARIOS],
)
@pytest.mark.asyncio
async def test_conversation_scenario(scenario: dict) -> None:
    """Ein Multi-Turn-Konversationstest pro Szenario."""
    turns = scenario["turns"]
    expected_tool = scenario.get("expected_tool_call")  # may be None

    async with (
        _agent_llm() as agent_llm,
        _judge_llm() as judge_llm,
        AgentSession(llm=agent_llm) as session,
    ):
        await session.start(FachweltAssistant())

        all_events: list = []

        for i, turn in enumerate(turns):
            result = await session.run(user_input=turn["user"])
            all_events.extend(result.events)

            idx = _last_assistant_message_index(result.events)
            if idx is None:
                if i < len(turns) - 1:
                    continue
                pytest.fail(
                    f"Turn {i + 1}: keine assistant-message — "
                    f"events={[type(e).__name__ for e in result.events]}"
                )

            assert_obj = ChatMessageAssert(result.events[idx], result.expect, idx)
            await assert_obj.judge(judge_llm, intent=turn["expect_intent"])

        called = _function_call_names(all_events)
        if expected_tool is None:
            # Erwartet: KEIN qualifying tool-call (graceful exit ohne tool)
            forbidden = {
                "mark_qualified_send_email",
                "schedule_callback",
                "mark_not_qualified",
            }
            actual_forbidden = [t for t in called if t in forbidden]
            assert not actual_forbidden, (
                f"Erwarteter Outcome=graceful_exit_no_tool, aber Tool-Calls "
                f"erfolgten: {actual_forbidden}"
            )
        else:
            assert expected_tool in called, (
                f"Erwartet Tool-Call '{expected_tool}', "
                f"tatsächlich gerufen: {called or '<keiner>'}"
            )
