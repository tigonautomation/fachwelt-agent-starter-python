"""Voice-Scout für Lisa (Fachwelt Voice-Agent) — Phase 2.

Pipeline pro Voice (sequentiell):
  1. Quality Render (eleven_multilingual_v2):
     - TTS → MP3
  2. Pronunciation Roundtrip:
     - STT (scribe_v1) → Transkript
     - Levenshtein.normalized_similarity() gegen normalisiertes Test-Skript
     - Pass: ratio >= 0.92
  3. TTFB Latency Test (eleven_flash_v2_5):
     - 3x streamed POST calls
     - Median TTFB ms (pass: <= 500ms)
  4. Write scoring.md

ENV: ELEVEN_API_KEY (aus .env.local)
Run: uv run python scripts/voice_scout_phase2.py
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

# Test script with proper umlauts
TEST_SCRIPT = (
    "Guten Tag, hier ist Lisa vom Fachwelt Verlag. "
    "Kurzer Hinweis: ich bin eine KI, das Gespraech wird "
    "zur Qualitaetssicherung aufgezeichnet. Wir bauen einen "
    "Marktplatz fuer B2B-Hersteller — haetten Sie zwei Minuten?"
)

# Models
QUALITY_MODEL = "eleven_multilingual_v2"
LATENCY_MODEL = "eleven_flash_v2_5"
STT_MODEL = "scribe_v1"

# Voice settings
VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.80,
    "style": 0.15,
    "use_speaker_boost": True,
    "speed": 1.0,
}

# Thresholds
LEV_THRESHOLD = 0.92
TTFB_THRESHOLD_MS = 500


def _normalize(s: str) -> str:
    """Normalize text for comparison (from pronunciation_audit.py)."""
    s = s.lower()
    s = re.sub(r"[^\w\säöüß-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_candidates_md(path: Path) -> list[tuple[str, str]]:
    """Parse top 15 voices from candidates.md.
    Returns: list of (voice_id, name) tuples.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Find markdown table start
    candidates = []
    in_table = False
    for line in lines:
        if "| voice_id | name |" in line:
            in_table = True
            continue
        if in_table and line.startswith("|"):
            # Skip header separator
            if "---|" in line:
                continue
            # Parse row
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                voice_id = parts[1].strip("`").strip()
                name = parts[2].strip()
                if voice_id and name:
                    candidates.append((voice_id, name))

    # Return top 15
    return candidates[:15]


def tts(text: str, voice_id: str, model_id: str, client: httpx.Client) -> bytes | None:
    """Render TTS."""
    url = f"{API_BASE}/text-to-speech/{voice_id}"
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": VOICE_SETTINGS,
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


def measure_ttfb(voice_id: str, client: httpx.Client) -> float | None:
    """Measure median TTFB over 3 streamed calls to flash model."""
    url = f"{API_BASE}/text-to-speech/{voice_id}/stream"
    body = {
        "text": TEST_SCRIPT,
        "model_id": LATENCY_MODEL,
        "voice_settings": VOICE_SETTINGS,
        "language_code": "de",
    }
    headers = {"xi-api-key": ELEVEN_API_KEY}

    ttfbs = []
    for attempt in range(3):
        try:
            start = time.time()
            with client.stream("POST", url, json=body, headers=headers, timeout=10.0) as r:
                if r.status_code >= 400:
                    if r.status_code == 401:
                        print(f"    [TTFB {attempt+1}] 401 — stopping.")
                        return None
                    if r.status_code == 429:
                        print(f"    [TTFB {attempt+1}] 429 — backoff + retry...")
                        time.sleep(5)
                        continue
                    print(f"    [TTFB {attempt+1}] {r.status_code}")
                    continue
                # Read first chunk
                for chunk in r.iter_bytes():
                    elapsed_ms = (time.time() - start) * 1000
                    ttfbs.append(elapsed_ms)
                    break
        except httpx.HTTPError as e:
            print(f"    [TTFB {attempt+1}] Error: {e}")
            continue

    if not ttfbs:
        return None
    ttfbs.sort()
    return ttfbs[len(ttfbs) // 2]  # median


def voice_id_to_slug(voice_id: str, name: str) -> str:
    """Create filename slug from voice_id and name."""
    name_slug = re.sub(r"[^\w]+", "_", name.lower())[:30].strip("_")
    return f"{voice_id}_{name_slug}"


def main() -> int:
    print("Voice-Scout Phase 2 — Starting...", flush=True)

    candidates_file = ROOT / "audit-results" / "voice-scout" / "candidates.md"
    if not candidates_file.exists():
        print(f"ERROR: {candidates_file} not found.")
        return 1

    candidates = parse_candidates_md(candidates_file)
    print(f"  → Parsed {len(candidates)} candidates from Phase 1", flush=True)

    if len(candidates) == 0:
        print("ERROR: No candidates found.")
        return 1

    # Output directory
    out_dir = ROOT / "audit-results" / "voice-scout"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Test each voice
    results = []
    with httpx.Client() as client:
        for idx, (voice_id, name) in enumerate(candidates, 1):
            print(f"\n[{idx}/{len(candidates)}] {name} ({voice_id})", flush=True)

            # A. Quality Render
            print(f"  → Rendering (quality model)...", flush=True)
            mp3 = tts(TEST_SCRIPT, voice_id, QUALITY_MODEL, client)
            if mp3 is None:
                print(f"  → SKIP: TTS failed")
                results.append({
                    "voice_id": voice_id,
                    "name": name,
                    "levenshtein_pct": None,
                    "ttfb_ms": None,
                    "pron_pass": False,
                    "latency_pass": False,
                    "skip_reason": "TTS render failed",
                })
                continue

            # Save MP3
            slug = voice_id_to_slug(voice_id, name)
            mp3_path = out_dir / f"{slug}.mp3"
            mp3_path.write_bytes(mp3)
            print(f"  → Saved: {mp3_path.name}")

            # B. Pronunciation roundtrip
            print(f"  → Running STT...", flush=True)
            transcript = stt(mp3, client)
            if transcript is None:
                print(f"  → SKIP: STT failed")
                results.append({
                    "voice_id": voice_id,
                    "name": name,
                    "levenshtein_pct": None,
                    "ttfb_ms": None,
                    "pron_pass": False,
                    "latency_pass": False,
                    "skip_reason": "STT failed",
                })
                continue

            lev_ratio = Levenshtein.normalized_similarity(_normalize(TEST_SCRIPT), _normalize(transcript))
            lev_pct = round(lev_ratio * 100, 1)
            pron_pass = lev_ratio >= LEV_THRESHOLD
            print(f"  → Levenshtein: {lev_pct}% {'✓' if pron_pass else '✗'}")

            # C. TTFB measurement
            print(f"  → Measuring latency (3 calls)...", flush=True)
            ttfb_ms = measure_ttfb(voice_id, client)
            if ttfb_ms is None:
                print(f"  → SKIP: TTFB measurement failed")
                results.append({
                    "voice_id": voice_id,
                    "name": name,
                    "levenshtein_pct": lev_pct,
                    "ttfb_ms": None,
                    "pron_pass": pron_pass,
                    "latency_pass": False,
                    "skip_reason": "TTFB measurement failed",
                })
                continue

            ttfb_ms = round(ttfb_ms, 0)
            latency_pass = ttfb_ms <= TTFB_THRESHOLD_MS
            print(f"  → TTFB median: {ttfb_ms}ms {'✓' if latency_pass else '✗'}")

            results.append({
                "voice_id": voice_id,
                "name": name,
                "levenshtein_pct": lev_pct,
                "ttfb_ms": ttfb_ms,
                "pron_pass": pron_pass,
                "latency_pass": latency_pass,
                "transcript": transcript,
                "skip_reason": None,
            })

    # Generate report
    print("\n=== Generating Report ===", flush=True)

    pron_pass_count = sum(1 for r in results if r["pron_pass"])
    latency_pass_count = sum(1 for r in results if r["latency_pass"])
    both_pass_count = sum(1 for r in results if r["pron_pass"] and r["latency_pass"])

    print(f"Pronunciation pass: {pron_pass_count}/{len(results)}")
    print(f"Latency pass: {latency_pass_count}/{len(results)}")
    print(f"Both pass: {both_pass_count}/{len(results)}")

    # Sort by levenshtein desc for top 5
    valid_results = [r for r in results if r["levenshtein_pct"] is not None]
    valid_results.sort(key=lambda x: x["levenshtein_pct"], reverse=True)

    # Write scoring.md
    today = datetime.now().date().isoformat()
    lines = [
        "# Voice-Scout Phase 2 — Render + Filter",
        "",
        f"**Date:** {today}",
        f"**Test script:** \"{TEST_SCRIPT}\"",
        f"**Quality render model:** {QUALITY_MODEL}",
        f"**Latency model:** {LATENCY_MODEL}",
        f"**Settings:** stability={VOICE_SETTINGS['stability']}, "
        f"similarity_boost={VOICE_SETTINGS['similarity_boost']}, "
        f"style={VOICE_SETTINGS['style']}, speed={VOICE_SETTINGS['speed']}",
        f"**Pronunciation pass:** Levenshtein ratio >= {LEV_THRESHOLD}",
        f"**Latency pass:** median TTFB <= {TTFB_THRESHOLD_MS}ms",
        "",
    ]

    # Warning if <5 both-pass
    if both_pass_count < 5:
        lines.append(
            f"> ⚠️ Nur {both_pass_count} Voices passten beide Gates. "
            "Top 5 nach Pronunciation alone — Latency manuell pruefen in Phase 3."
        )
        lines.append("")

    # Results table
    lines += [
        "## Results",
        "",
        "| # | voice_id | name | levenshtein% | ttfb_ms (median) | pron_pass | latency_pass | overall |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for i, r in enumerate(results, 1):
        vid = r["voice_id"]
        name = r["name"]
        lev = f"{r['levenshtein_pct']}%" if r["levenshtein_pct"] is not None else "—"
        ttfb = f"{int(r['ttfb_ms'])}" if r["ttfb_ms"] is not None else "—"
        pron = "✓" if r["pron_pass"] else "✗"
        latency = "✓" if r["latency_pass"] else "✗"

        if r["skip_reason"]:
            overall = f"SKIP ({r['skip_reason']})"
        elif r["pron_pass"] and r["latency_pass"]:
            overall = "PASS"
        elif r["pron_pass"]:
            overall = "PRON-PASS"
        elif r["latency_pass"]:
            overall = "LATENCY-PASS"
        else:
            overall = "FAIL"

        lines.append(
            f"| {i} | `{vid}` | {name} | {lev} | {ttfb} | {pron} | {latency} | {overall} |"
        )

    # Top 5 section
    lines += [
        "",
        "## Top 5 (by levenshtein% among valid voices)",
        "",
    ]

    top_5 = valid_results[:5]
    if top_5:
        for i, r in enumerate(top_5, 1):
            lines.append(
                f"{i}. `{r['voice_id']}` — {r['name']} — "
                f"{r['levenshtein_pct']}% "
                f"— {int(r['ttfb_ms']) if r['ttfb_ms'] else '—'}ms"
            )
    else:
        lines.append("(None with valid levenshtein measurements)")

    # Failures section
    fails = [r for r in results if r["skip_reason"]]
    if fails:
        lines += [
            "",
            "## Skipped",
            "",
            "| voice_id | name | reason |",
            "|---|---|---|",
        ]
        for r in fails:
            lines.append(
                f"| `{r['voice_id']}` | {r['name']} | {r['skip_reason']} |"
            )

    # Pronunciation mismatches
    pron_fails = [r for r in results if not r["pron_pass"] and r["levenshtein_pct"] is not None]
    if pron_fails:
        lines += [
            "",
            "## Pronunciation Issues (ratio < 0.92)",
            "",
        ]
        for r in pron_fails[:5]:  # top 5 by lev pct
            lines.append(f"- `{r['voice_id']}` ({r['name']}): {r['levenshtein_pct']}%")
            lines.append(f"  Heard: \"{r['transcript'][:100]}\"")

    out_file = out_dir / "scoring.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ Report written: {out_file}", flush=True)

    # Summary
    print("")
    print("=== Phase 2 Complete ===")
    print(f"Voices tested: {len(results)}")
    print(f"Pronunciation pass: {pron_pass_count}")
    print(f"Latency pass: {latency_pass_count}")
    print(f"Both pass: {both_pass_count}")
    if top_5:
        print(f"Top 5: {', '.join(r['name'] for r in top_5)}")
    print(f"Output: {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
