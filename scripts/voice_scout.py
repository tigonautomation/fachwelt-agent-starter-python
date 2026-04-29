"""Voice-Scout für Lisa (Fachwelt Voice-Agent) — Phase 1.

Pipeline:
  1. ElevenLabs /v1/shared-voices API — zwei Queries parallel (professional + high_quality)
  2. Dedupe & Filter:
     - language == "de"
     - category in {professional, high_quality} OR use_case in {conversational}
     - cloned_by_count >= 50 (Fallback: >= 20 wenn <10 Kandidaten)
     - description contains ≥1 von {warm, natural, conversational, friendly, sales}
       AND kein {news, narration, audiobook, asmr, character}
  3. Sort by cloned_by_count DESC, top 20
  4. Write candidates.md

ENV: ELEVEN_API_KEY (aus .env.local)
Run: uv run python scripts/voice_scout.py
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")

ELEVEN_API_KEY = os.environ["ELEVEN_API_KEY"]
API_BASE = "https://api.elevenlabs.io/v1"

POSITIVE_KEYWORDS = {"warm", "natural", "conversational", "friendly", "sales"}
NEGATIVE_KEYWORDS = {"news", "narration", "audiobook", "asmr", "character"}


def fetch_voices(query_params: dict) -> list[dict]:
    """Fetch shared voices from ElevenLabs."""
    url = f"{API_BASE}/shared-voices"
    headers = {"xi-api-key": ELEVEN_API_KEY}

    try:
        r = httpx.get(url, params=query_params, headers=headers, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        return data.get("voices", [])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print(f"ERROR 401: API key invalid or permission denied.")
            raise SystemExit(1)
        elif e.response.status_code == 429:
            print(f"ERROR 429: Rate limited. Backoff 5s, retry once...")
            import time

            time.sleep(5)
            try:
                r = httpx.get(url, params=query_params, headers=headers, timeout=30.0)
                r.raise_for_status()
                data = r.json()
                return data.get("voices", [])
            except httpx.HTTPStatusError as e2:
                if e2.response.status_code == 429:
                    print(f"ERROR 429 (retry): Still rate limited. Stopping.")
                    raise SystemExit(1)
                raise
        raise


def filter_voices(voices: list[dict], min_uses: int = 50) -> list[dict]:
    """Apply filter pipeline."""
    candidates = []

    for v in voices:
        # Filter 1: language == "de"
        if v.get("language") != "de":
            continue

        # Filter 2: category or use_case
        category = v.get("category", "")
        use_case = v.get("use_case", "")
        if category not in {"professional", "high_quality"} and use_case != "conversational":
            continue

        # Filter 3: cloned_by_count >= min_uses
        if v.get("cloned_by_count", 0) < min_uses:
            continue

        # Filter 4: description keywords
        desc = (v.get("description") or "").lower()
        has_positive = any(kw in desc for kw in POSITIVE_KEYWORDS)
        has_negative = any(kw in desc for kw in NEGATIVE_KEYWORDS)

        if has_positive and not has_negative:
            candidates.append(v)

    return candidates


def main() -> int:
    print("Voice-Scout Phase 1 — Starting...", flush=True)

    # Fetch from both query profiles
    print("  → Fetching professional voices...", flush=True)
    query_a = {"language": "de", "gender": "female", "category": "professional", "page_size": 100}
    voices_a = fetch_voices(query_a)
    print(f"    Received {len(voices_a)} voices (professional)")

    print("  → Fetching high_quality voices...", flush=True)
    query_b = {"language": "de", "gender": "female", "category": "high_quality", "page_size": 100}
    voices_b = fetch_voices(query_b)
    print(f"    Received {len(voices_b)} voices (high_quality)")

    # Dedupe by voice_id
    all_voices = voices_a + voices_b
    seen = set()
    deduped = []
    for v in all_voices:
        vid = v.get("voice_id")
        if vid not in seen:
            seen.add(vid)
            deduped.append(v)

    print(f"  → After dedup: {len(deduped)} unique voices", flush=True)

    # Filter with min_uses=50
    candidates = filter_voices(deduped, min_uses=50)
    print(f"  → After filtering (uses≥50): {len(candidates)} candidates", flush=True)

    # Fallback: if <10, retry with min_uses=20
    retry_flag = False
    if len(candidates) < 10:
        print(f"  → Less than 10 candidates; retry with uses≥20", flush=True)
        candidates = filter_voices(deduped, min_uses=20)
        retry_flag = True
        print(f"  → After retry (uses≥20): {len(candidates)} candidates", flush=True)

    # Sort by cloned_by_count DESC, take top 20
    candidates.sort(key=lambda x: x.get("cloned_by_count", 0), reverse=True)
    candidates = candidates[:20]

    # Write candidates.md
    out_dir = ROOT / "audit-results" / "voice-scout"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "candidates.md"

    today = datetime.now().date().isoformat()
    lines = [
        "# Voice-Scout Candidates — Phase 1",
        "",
        f"**Date:** {today}",
        "**Source:** ElevenLabs `/v1/shared-voices` (category=professional + category=high_quality)",
        "**Filter:** German, female, ≥50 uses, conversational/warm description, no news/audiobook/character",
        "",
    ]

    if retry_flag:
        lines.append(
            f"> ⚠️ Retry mit `uses≥20` notwendig — strenger Filter ergab nur wenige Kandidaten."
        )
        lines.append("")

    if len(candidates) < 10:
        lines.append(f"> 🔴 Nur {len(candidates)} Kandidaten gefunden.")
        lines.append(
            "> User-Decision: Filter weiter loosen oder mit {len(candidates)} fortfahren?"
        )
        lines.append("")

    lines += [
        f"**Result:** {len(candidates)} candidates",
        "",
        "| voice_id | name | accent | use_case | uses | description (kurz) | preview |",
        "|---|---|---|---|---|---|---|",
    ]

    for v in candidates:
        voice_id = v.get("voice_id", "?")
        name = v.get("name", "?")
        accent = v.get("accent", "?")
        use_case = v.get("use_case", "?")
        uses = v.get("cloned_by_count", 0)
        desc = v.get("description", "")[:80].replace("|", "\\|")
        preview_url = v.get("preview_url", "")
        preview_cell = f"[mp3]({preview_url})" if preview_url else "—"

        lines.append(
            f"| `{voice_id}` | {name} | {accent} | {use_case} | {uses} | {desc} | {preview_cell} |"
        )

    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ Report written: {out_file}", flush=True)

    # Summary
    print("")
    print(f"=== Phase 1 Complete ===")
    print(f"Candidates found: {len(candidates)}")
    print(f"Retry needed: {retry_flag}")
    if len(candidates) > 0:
        names = [c.get("name", "?") for c in candidates[:5]]
        print(f"Top 5: {', '.join(names)}")
    print(f"Output: {out_file}")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
