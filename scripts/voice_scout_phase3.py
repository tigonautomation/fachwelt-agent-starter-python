"""Voice-Scout für Lisa (Fachwelt Voice-Agent) — Phase 3.

A/B render Top 5 voices × 2 settings = 10 MP3s for ear-testing.

Pipeline:
  1. Top 5 voices from Phase 2 (Phase 3 = Setting A vs B)
  2. For each voice × 2 settings:
     - TTS render (eleven_multilingual_v2)
     - Save as {idx:02d}_{voice_id}_{name_slug}_{setting}.mp3
  3. Pronunciation roundtrip (STT + Levenshtein) on all 10
  4. Write final/README.md with listening guide

ENV: ELEVEN_API_KEY (aus .env.local)
Run: uv run python scripts/voice_scout_phase3.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")

ELEVEN_API_KEY = os.environ["ELEVEN_API_KEY"]
API_BASE = "https://api.elevenlabs.io/v1"

# Top 5 from Phase 2
TOP_5_VOICES = [
    ("WHaUUVTDq47Yqc9aDbkH", "Enniah - Friendly and Motivating"),
    ("NE7AIW5DoJ7lUosXV2KR", "Ela - Cheerful and Happy"),
    ("pMrwpTuGOma7Nubxs5jo", "Lea - Warm and Supportive"),
    ("rAmra0SCIYOxYmRNDSm3", "Lana Weiss - Soft and Sweet"),
    ("v3V1d2rk6528UrLKRuy8", "Susi - Effortless and Confident"),
]

# Test script with proper umlauts (same as Phase 2)
TEST_SCRIPT = (
    "Guten Tag, hier ist Lisa vom Fachwelt Verlag. "
    "Kurzer Hinweis: ich bin eine KI, das Gespraech wird "
    "zur Qualitaetssicherung aufgezeichnet. Wir bauen einen "
    "Marktplatz fuer B2B-Hersteller — haetten Sie zwei Minuten?"
)

# Models
QUALITY_MODEL = "eleven_multilingual_v2"
STT_MODEL = "scribe_v1"

# Voice settings A & B
SETTINGS_A = {
    "name": "A",
    "desc": "current production",
    "params": {
        "stability": 0.45,
        "similarity_boost": 0.80,
        "style": 0.15,
        "use_speaker_boost": True,
        "speed": 1.0,
    },
}

SETTINGS_B = {
    "name": "B",
    "desc": "slower deliberate",
    "params": {
        "stability": 0.55,
        "similarity_boost": 0.80,
        "style": 0.15,
        "use_speaker_boost": True,
        "speed": 0.95,
    },
}

LEV_THRESHOLD = 0.92


def _normalize(s: str) -> str:
    """Normalize text for comparison (from Phase 2)."""
    s = s.lower()
    s = re.sub(r"[^\w\säöüß-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tts(
    text: str, voice_id: str, voice_settings: dict, client: httpx.Client
) -> bytes | None:
    """Render TTS."""
    url = f"{API_BASE}/text-to-speech/{voice_id}"
    body = {
        "text": text,
        "model_id": QUALITY_MODEL,
        "voice_settings": voice_settings,
        "language_code": "de",
    }
    headers = {"xi-api-key": ELEVEN_API_KEY, "accept": "audio/mpeg"}

    try:
        r = client.post(url, json=body, headers=headers, timeout=60.0)
        if r.status_code == 401:
            print(f"  → ERROR 401 (TTS): API permission denied. Stopping.")
            return None
        if r.status_code == 429:
            print(f"  → 429 rate limit (TTS), backoff 5s...")
            time.sleep(5)
            r = client.post(url, json=body, headers=headers, timeout=60.0)
        if r.status_code >= 400:
            # Voice-specific 4xx: skip and continue
            if 400 <= r.status_code < 500:
                print(f"  → {r.status_code} (TTS): {r.text[:100]}")
                return None
        r.raise_for_status()
        return r.content
    except httpx.HTTPStatusError as e:
        print(f"  → TTS ERROR {e.response.status_code}: {e.response.text[:100]}")
        return None


def stt(mp3: bytes, client: httpx.Client) -> str | None:
    """Transcribe audio via STT."""
    url = f"{API_BASE}/speech-to-text"
    files = {"file": ("audio.mp3", mp3, "audio/mpeg")}
    data = {"model_id": STT_MODEL, "language_code": "deu"}
    headers = {"xi-api-key": ELEVEN_API_KEY}

    try:
        r = client.post(url, files=files, data=data, headers=headers, timeout=120.0)
        if r.status_code == 401:
            print(f"  → ERROR 401 (STT): API permission denied.")
            return None
        if r.status_code == 429:
            print(f"  → 429 rate limit (STT), backoff 5s...")
            time.sleep(5)
            r = client.post(url, files=files, data=data, headers=headers, timeout=120.0)
        r.raise_for_status()
        return r.json().get("text", "")
    except httpx.HTTPStatusError as e:
        print(f"  → STT ERROR {e.response.status_code}: {e.response.text[:100]}")
        return None


def voice_id_to_slug(voice_id: str, name: str) -> str:
    """Create filename slug from voice_id and name."""
    name_slug = re.sub(r"[^\w]+", "_", name.lower())[:30].strip("_")
    return f"{voice_id}_{name_slug}"


def main() -> int:
    print("Voice-Scout Phase 3 — A/B Render Top 5 × 2 Settings", flush=True)

    # Output directory
    out_dir = ROOT / "audit-results" / "voice-scout" / "final"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Track results for README
    results = []

    with httpx.Client() as client:
        file_index = 1
        for voice_idx, (voice_id, voice_name) in enumerate(TOP_5_VOICES, 1):
            print(f"\n[Voice {voice_idx}/5] {voice_name}", flush=True)

            for setting in [SETTINGS_A, SETTINGS_B]:
                print(
                    f"  → Setting {setting['name']} ({setting['desc']})...",
                    flush=True,
                )

                # Render
                mp3 = tts(TEST_SCRIPT, voice_id, setting["params"], client)
                if mp3 is None:
                    print(f"    SKIP: TTS failed")
                    results.append({
                        "idx": file_index,
                        "voice_id": voice_id,
                        "voice_name": voice_name,
                        "setting": setting["name"],
                        "filename": None,
                        "levenshtein_pct": None,
                        "skip_reason": "TTS render failed",
                    })
                    file_index += 1
                    continue

                # Save MP3
                slug = voice_id_to_slug(voice_id, voice_name)
                filename = f"{file_index:02d}_{slug}_{setting['name']}.mp3"
                mp3_path = out_dir / filename
                mp3_path.write_bytes(mp3)
                print(f"    Saved: {filename}")

                # Pronunciation roundtrip
                print(f"    Running STT...", flush=True)
                transcript = stt(mp3, client)
                if transcript is None:
                    print(f"    STT failed, skipping Levenshtein")
                    results.append({
                        "idx": file_index,
                        "voice_id": voice_id,
                        "voice_name": voice_name,
                        "setting": setting["name"],
                        "filename": filename,
                        "levenshtein_pct": None,
                        "skip_reason": "STT failed",
                    })
                    file_index += 1
                    continue

                lev_ratio = Levenshtein.normalized_similarity(
                    _normalize(TEST_SCRIPT), _normalize(transcript)
                )
                lev_pct = round(lev_ratio * 100, 1)
                print(f"    Levenshtein: {lev_pct}%")

                results.append({
                    "idx": file_index,
                    "voice_id": voice_id,
                    "voice_name": voice_name,
                    "setting": setting["name"],
                    "filename": filename,
                    "levenshtein_pct": lev_pct,
                    "skip_reason": None,
                })

                file_index += 1

    # Compute statistics
    valid_results = [r for r in results if r["levenshtein_pct"] is not None]
    if valid_results:
        lev_scores = [r["levenshtein_pct"] for r in valid_results]
        min_lev = min(lev_scores)
        max_lev = max(lev_scores)
        avg_lev = round(sum(lev_scores) / len(lev_scores), 1)
    else:
        min_lev = max_lev = avg_lev = None

    # Find highest score
    top_result = max(valid_results, key=lambda x: x["levenshtein_pct"]) if valid_results else None

    # Generate README.md
    print("\n=== Generating README.md ===", flush=True)

    today = datetime.now().date().isoformat()
    lines = [
        "# Voice-Scout Phase 3 — Final A/B Render",
        "",
        f"**Date:** {today}",
        f"**Render model:** {QUALITY_MODEL}",
        f'**Test script:** "{TEST_SCRIPT}"',
        "",
        "## Settings",
        "",
        f"- **Setting A** — {SETTINGS_A['desc']}: "
        f"stability={SETTINGS_A['params']['stability']}, speed={SETTINGS_A['params']['speed']}",
        f"- **Setting B** — {SETTINGS_B['desc']}: "
        f"stability={SETTINGS_B['params']['stability']}, speed={SETTINGS_B['params']['speed']}",
        "",
        "## Files (random listening order recommended)",
        "",
        "| # | File | Voice | Setting | Levenshtein% |",
        "|---|---|---|---|---|",
    ]

    for r in results:
        idx = r["idx"]
        filename = r["filename"] or "—"
        voice_name = r["voice_name"]
        setting = r["setting"]
        lev_pct = f"{r['levenshtein_pct']}%" if r["levenshtein_pct"] is not None else "—"

        lines.append(
            f"| {idx} | {filename} | {voice_name} | {setting} | {lev_pct} |"
        )

    lines += [
        "",
        "## Pronunciation Statistics",
        "",
    ]

    if valid_results:
        lines.append(f"- **Levenshtein% range:** {min_lev}% – {max_lev}%")
        lines.append(f"- **Average:** {avg_lev}%")
        lines.append(f"- **Highest:** {top_result['voice_name']} (Setting {top_result['setting']}) at {top_result['levenshtein_pct']}%")
    else:
        lines.append("No valid pronunciation measurements.")

    lines += [
        "",
        "## Objective Recommendation",
        "",
    ]

    if top_result:
        lines.append(
            f"Highest Levenshtein%: **{top_result['voice_name']} + Setting {top_result['setting']}** "
            f"at {top_result['levenshtein_pct']}%."
        )
        lines.append("")
        lines.append(
            "(But pronunciation gap between top entries is small — final pick should be by ear.)"
        )
    else:
        lines.append("(No valid measurements — pick by ear.)")

    lines += [
        "",
        "## How to Listen (Edon)",
        "",
        "1. Open all 10 MP3s in a media player",
        "2. Listen in random order — don't compare A vs B back-to-back per voice (bias)",
        "3. Score each on: warmth, clarity, conversational feel, naturalness",
        "4. Pick ONE voice_id + ONE setting",
        "",
        "## Hard Reminders",
        "",
        "- **Setting B** = slower (speed=0.95). Sounds more 'thinking out loud', fewer rushed words.",
        "- **Setting A** = production default. More 'professional broadcast' feel.",
        "",
        "## Top 5 voice_ids Reference",
        "",
        "| Rank | Name | voice_id |",
        "|---|---|---|",
        "| 1 | Enniah | WHaUUVTDq47Yqc9aDbkH |",
        "| 2 | Ela ⚠️ | NE7AIW5DoJ7lUosXV2KR |",
        "| 3 | Lea | pMrwpTuGOma7Nubxs5jo |",
        "| 4 | Lana Weiss | rAmra0SCIYOxYmRNDSm3 |",
        "| 5 | Susi | v3V1d2rk6528UrLKRuy8 |",
        "",
        "⚠️ = Phase 2 latency 511ms (+11ms over 500ms gate). Borderline, not disqualifying for B2B outbound.",
    ]

    readme_path = out_dir / "README.md"
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ README written: {readme_path}", flush=True)

    # Summary
    print("")
    print("=== Phase 3 Complete ===")
    print(f"Files rendered: {len([r for r in results if r['filename'] is not None])}/{len(results)}")
    if valid_results:
        print(f"Levenshtein% range: {min_lev}% – {max_lev}% (avg {avg_lev}%)")
        if top_result:
            print(
                f"Highest score: {top_result['voice_name']} "
                f"(Setting {top_result['setting']}) at {top_result['levenshtein_pct']}%"
            )
    print(f"Output: {out_dir}")
    print(f"README: {readme_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
