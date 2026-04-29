"""Pronunciation Audit für Lisa (Fachwelt Voice-Agent).

Pipeline pro Wort/Phrase:
  1. ElevenLabs TTS (mit produktiven Voice-Settings + Pronunciation-Dict aus src/agent.py)
     → MP3
  2. ElevenLabs Speech-to-Text (scribe_v1) → Transkript
  3. Diff: pass wenn mind. ein erwartetes Token (normalisiert) im Transkript erscheint
  4. Markdown-Tabelle nach audit-results/pronunciation-YYYY-MM-DD.md

Input:  tests/pronunciation_words.yaml
ENV:    ELEVEN_API_KEY (aus .env.local)

Run:    uv run python scripts/pronunciation_audit.py
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

# Reuse Voice-Stack-Konstanten aus dem Agent — single source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agent import (  # noqa: E402
    ELEVENLABS_MODEL,
    FACHWELT_PRONUNCIATION_DICT,
    PHONIO_VOICE_SETTINGS,
    TTS_VOICE_ID,
)

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")

ELEVEN_API_KEY = os.environ["ELEVEN_API_KEY"]
TTS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{TTS_VOICE_ID}"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
STT_MODEL = "scribe_v1"

WORDS_FILE = ROOT / "tests" / "pronunciation_words.yaml"
OUT_DIR = ROOT / "audit-results"


def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\säöüß-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _settings_payload() -> dict:
    return {
        "stability": PHONIO_VOICE_SETTINGS.stability,
        "similarity_boost": PHONIO_VOICE_SETTINGS.similarity_boost,
        "style": PHONIO_VOICE_SETTINGS.style,
        "use_speaker_boost": PHONIO_VOICE_SETTINGS.use_speaker_boost,
        "speed": PHONIO_VOICE_SETTINGS.speed,
    }


def _pronunciation_payload() -> list[dict]:
    return [
        {
            "pronunciation_dictionary_id": d.pronunciation_dictionary_id,
            "version_id": d.version_id,
        }
        for d in FACHWELT_PRONUNCIATION_DICT
    ]


def tts(text: str, client: httpx.Client) -> bytes:
    body = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": _settings_payload(),
        "pronunciation_dictionary_locators": _pronunciation_payload(),
        "language_code": "de",
    }
    headers = {"xi-api-key": ELEVEN_API_KEY, "accept": "audio/mpeg"}
    r = client.post(TTS_URL, json=body, headers=headers, timeout=60.0)
    r.raise_for_status()
    return r.content


def stt(mp3: bytes, client: httpx.Client) -> str:
    files = {"file": ("audio.mp3", mp3, "audio/mpeg")}
    data = {"model_id": STT_MODEL, "language_code": "deu"}
    headers = {"xi-api-key": ELEVEN_API_KEY}
    r = client.post(STT_URL, files=files, data=data, headers=headers, timeout=120.0)
    r.raise_for_status()
    return r.json().get("text", "")


def evaluate(expected_tokens: list[str], transcript: str) -> tuple[bool, str | None]:
    norm_transcript = _normalize(transcript)
    for token in expected_tokens:
        if _normalize(token) in norm_transcript:
            return True, token
    return False, None


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    audio_dir = OUT_DIR / f"audio-{today}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    words = yaml.safe_load(WORDS_FILE.read_text(encoding="utf-8"))

    rows = []
    pass_count = 0
    stt_skipped = False

    with httpx.Client() as client:
        for i, item in enumerate(words, 1):
            text = item["text"]
            expected = item["expect_contains"]
            print(f"[{i}/{len(words)}] {text!r}", flush=True)

            try:
                mp3 = tts(text, client)
            except httpx.HTTPStatusError as e:
                print(f"  TTS ERROR {e.response.status_code}: {e.response.text[:200]}")
                rows.append((text, expected, "<TTS-Fehler>", "ERROR", None, None))
                continue

            slug = re.sub(r"[^\w]+", "_", _normalize(text))[:60].strip("_")
            audio_path = audio_dir / f"{i:02d}_{slug}.mp3"
            audio_path.write_bytes(mp3)

            transcript: str | None = None
            if not stt_skipped:
                try:
                    transcript = stt(mp3, client)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 401 and "speech_to_text" in e.response.text:
                        print("  STT permission missing — skipping STT for remaining items.")
                        stt_skipped = True
                    else:
                        print(f"  STT ERROR {e.response.status_code}: {e.response.text[:200]}")

            if transcript is None:
                rows.append(
                    (text, expected, "<STT übersprungen>", "PENDING", None, audio_path.name)
                )
                print(f"  → MP3 saved: {audio_path.name}")
                continue

            ok, matched = evaluate(expected, transcript)
            status = "PASS" if ok else "FAIL"
            if ok:
                pass_count += 1
            rows.append(
                (text, expected, transcript.strip(), status, matched, audio_path.name)
            )
            print(f"  → {status}: heard {transcript.strip()!r}")

    out_file = OUT_DIR / f"pronunciation-{today}.md"
    pending = sum(1 for r in rows if r[3] == "PENDING")
    lines = [
        f"# Pronunciation Audit — {today}",
        "",
        f"**Voice:** Johanna (`{TTS_VOICE_ID}`)  ",
        f"**Model:** `{ELEVENLABS_MODEL}`  ",
        f"**Settings:** stability={PHONIO_VOICE_SETTINGS.stability}, "
        f"similarity={PHONIO_VOICE_SETTINGS.similarity_boost}, "
        f"style={PHONIO_VOICE_SETTINGS.style}, speed={PHONIO_VOICE_SETTINGS.speed}  ",
        f"**Pronunciation Dict:** "
        f"{FACHWELT_PRONUNCIATION_DICT[0].pronunciation_dictionary_id} "
        f"(v {FACHWELT_PRONUNCIATION_DICT[0].version_id})  ",
        f"**MP3 directory:** `{audio_dir.relative_to(ROOT)}/`",
        "",
        f"**Result:** {pass_count}/{len(words)} PASS"
        + (f", {pending} PENDING (STT permission fehlt)" if pending else ""),
        "",
    ]
    if stt_skipped:
        lines += [
            "> ⚠️ **STT-Permission fehlt:** ElevenLabs API-Key hat keine "
            "`speech_to_text`-Berechtigung. MP3s liegen unter "
            f"`{audio_dir.relative_to(ROOT)}/` für manuelles Anhören. "
            "Edon: Permission auf elevenlabs.io/account → API Keys aktivieren, "
            "dann re-run für automatischen Diff.",
            "",
        ]
    lines += [
        "| # | Phrase | Erwartet (mind. eines) | Gehört (STT) | Status | MP3 |",
        "|---|---|---|---|---|---|",
    ]
    for i, (text, expected, transcript, status, _, audio) in enumerate(rows, 1):
        exp_str = " / ".join(f"`{e}`" for e in expected)
        audio_cell = f"`{audio}`" if audio else "—"
        lines.append(
            f"| {i} | {text} | {exp_str} | {transcript[:100]} | "
            f"**{status}** | {audio_cell} |"
        )

    fails = [(t, e, h) for t, e, h, s, _, _ in rows if s == "FAIL"]
    if fails:
        lines += ["", "## Fail-Analyse — Alias-Vorschläge", ""]
        for text, expected, heard in fails:
            lines.append(f"- `{text}`")
            lines.append(f"  - Erwartet: {expected}")
            lines.append(f"  - Gehört: `{heard}`")
            lines.append(
                "  - **Vorschlag:** PLS-Alias in Dict "
                "`IW2jbKiFNq8Nz0X4kscp` ergänzen, neue Version-ID "
                "in `src/agent.py` einsetzen. Edon's Decision — nicht automatisch."
            )

    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {pass_count}/{len(words)} PASS, {pending} PENDING — Report: {out_file}")
    return 0 if pass_count == len(words) else 1


if __name__ == "__main__":
    sys.exit(main())
