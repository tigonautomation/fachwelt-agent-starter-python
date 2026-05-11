"""Opener audio + text helpers for the Fachwelt Call.

The Opener is the first utterance of every Call. For the default text we
play a pre-rendered MP3 (zero TTS variance, bit-for-bit identical across
calls). For custom dashboard openers we fall back to live TTS.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import av
from livekit import rtc

OPENER_AUDIO_PATH = Path(__file__).resolve().parent.parent / "assets" / "opener.mp3"
OPENER_SAMPLE_RATE = 24000

OPENER_TEXT = (
    "Guten Tag, hier ist Lisa vom Fachwelt Verlag. "
    "Kurzer Hinweis: ich bin eine KI, das Gespräch wird "
    "zur Qualitätssicherung aufgezeichnet. Wir bauen einen "
    "Marktplatz für B2B-Hersteller — hätten Sie zwei Minuten?"
)


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


def validate_opener_audio() -> None:
    """Raise if the opener MP3 is missing or unreadable.

    LK Agents swallows audio-iterator exceptions via @log_exceptions, so we
    pre-flight the file before passing the iterator to `session.say` (A3
    silent-fail fix).
    """
    if not OPENER_AUDIO_PATH.exists():
        raise FileNotFoundError(f"opener missing at {OPENER_AUDIO_PATH}")
    container = av.open(str(OPENER_AUDIO_PATH))
    container.close()
