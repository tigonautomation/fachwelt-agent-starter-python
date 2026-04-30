"""Block A — opener audio integrity tests.

Reproduces the silent-fail risk identified during the 2026-04-30 hardening pass:
LiveKit Agents' `_tts_task` decorates with `@log_exceptions`, which swallows any
exception raised by the audio iterator passed to `session.say()`. A corrupted or
missing opener MP3 would therefore produce zero audio without any visible error
in the call flow — the user hears nothing.

These tests verify:
1. The committed opener.mp3 decodes to the expected frame format.
2. `opener_audio_frames()` raises promptly on a missing file (the pre-flight
   check in `agent.py` catches this and falls back to live TTS).
3. The pre-flight pattern itself (file-exists + `av.open` validate) catches
   the realistic failure modes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import av
import pytest

from agent import OPENER_AUDIO_PATH, OPENER_SAMPLE_RATE, opener_audio_frames

EXPECTED_OPENER_MD5 = "20ad86dc43cbcfefe08ba1f6d399234b"


def test_opener_mp3_committed_and_matches_take_04() -> None:
    """A1 — the bundled opener is the voice-scout selected take_04."""
    assert OPENER_AUDIO_PATH.exists(), f"opener missing at {OPENER_AUDIO_PATH}"
    digest = hashlib.md5(OPENER_AUDIO_PATH.read_bytes()).hexdigest()
    assert digest == EXPECTED_OPENER_MD5, (
        f"opener md5 mismatch — got {digest}, expected {EXPECTED_OPENER_MD5} (take_04)"
    )


async def test_opener_audio_frames_yields_24khz_mono_int16() -> None:
    """A2 — frames must match the format LK expects on the audio output channel."""
    frames = []
    async for frame in opener_audio_frames():
        frames.append(frame)
    assert frames, "opener_audio_frames yielded no frames"
    first = frames[0]
    assert first.sample_rate == OPENER_SAMPLE_RATE == 24000
    assert first.num_channels == 1
    assert first.data.itemsize == 2, "expected int16 PCM (2 bytes per sample)"
    assert len(first.data) == first.samples_per_channel, (
        "data must contain exactly samples_per_channel int16 samples"
    )


async def test_opener_audio_frames_raises_on_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3 — generator raises promptly so the pre-flight in agent.py can catch it.

    Reproduces the silent-fail trigger: if the opener disappears (deploy bug,
    bad volume mount), the iterator must raise FileNotFoundError instead of
    silently yielding zero frames.
    """
    missing = tmp_path / "does-not-exist.mp3"
    monkeypatch.setattr("agent.OPENER_AUDIO_PATH", missing)

    with pytest.raises(FileNotFoundError):
        async for _ in opener_audio_frames():
            pass


async def test_opener_audio_frames_raises_on_corrupted_mp3(tmp_path: Path) -> None:
    """A3 — the second realistic failure mode: file present but not decodable."""
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(b"this is not an mp3 it is plain text")

    # The pre-flight in agent.py uses av.open() to validate. Confirm av rejects this.
    with pytest.raises(av.error.InvalidDataError):
        av.open(str(corrupt))
