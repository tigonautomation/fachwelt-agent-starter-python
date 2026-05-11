"""Pre-Render Opener für Lisa.

Rendert 5 Takes des Openers mit Live-Settings (stability 0.80, style 0.30).
Operator hört sie an und pickt den besten — der wandert nach assets/opener.mp3
und wird im Live-Agent statt session.say() abgespielt.

ENV: ELEVEN_API_KEY (aus .env.local)
Run: uv run python scripts/render_opener_takes.py
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")

ELEVEN_API_KEY = os.environ["ELEVEN_API_KEY"]
API_BASE = "https://api.elevenlabs.io/v1"

VOICE_ID = "v3V1d2rk6528UrLKRuy8"  # Susi
MODEL_ID = "eleven_multilingual_v2"

OPENER_TEXT = (
    "Guten Tag, hier ist Lisa vom Fachwelt Verlag. "
    "Kurzer Hinweis: ich bin eine KI, das Gespräch wird "
    "zur Qualitätssicherung aufgezeichnet. Wir bauen einen "
    "Marktplatz für B2B-Hersteller — hätten Sie zwei Minuten?"
)

VOICE_SETTINGS = {
    "stability": 0.80,
    "similarity_boost": 0.80,
    "style": 0.30,
    "use_speaker_boost": True,
    "speed": 0.95,
}

NUM_TAKES = 5
OUT_DIR = ROOT / "assets" / "opener-takes"


def render_take(take_idx: int) -> Path:
    url = f"{API_BASE}/text-to-speech/{VOICE_ID}"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": OPENER_TEXT,
        "model_id": MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
    }
    out_path = OUT_DIR / f"take_{take_idx:02d}.mp3"
    with httpx.stream("POST", url, json=payload, headers=headers, timeout=60.0) as r:
        r.raise_for_status()
        with out_path.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"  take {take_idx} -> {out_path.relative_to(ROOT)}")
    return out_path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Rendering {NUM_TAKES} opener takes")
    print(f"  voice: {VOICE_ID} (Susi)")
    print(f"  model: {MODEL_ID}")
    print(f"  settings: stability={VOICE_SETTINGS['stability']}, "
          f"style={VOICE_SETTINGS['style']}, speed={VOICE_SETTINGS['speed']}")
    print()
    for i in range(1, NUM_TAKES + 1):
        render_take(i)
    print()
    print(f"Done. Listen to {OUT_DIR.relative_to(ROOT)}/take_*.mp3 and pick the best.")
    print(f"Then: cp {OUT_DIR.relative_to(ROOT)}/take_NN.mp3 assets/opener.mp3")


if __name__ == "__main__":
    main()
