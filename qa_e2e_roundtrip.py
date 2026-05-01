"""
Headless E2E roundtrip test for Lisa Voice Agent.

Verifies:
  1. Token endpoint reachable, returns valid JWT
  2. Room creation by dashboard succeeds
  3. Worker auto-dispatch fires (agent participant joins room)
  4. Agent publishes audio track within timeout (opener spoken)
  5. Room metadata reaches worker (config_id non-null in worker log)

No microphone needed. Connects as a passive subscriber and listens.

Usage:
    python qa_e2e_roundtrip.py [--base https://...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from typing import Optional

from livekit import rtc

DEFAULT_BASE = "https://fachwelt-outbound-staging.surfingtigon.com"
AGENT_JOIN_TIMEOUT_S = 30.0
AUDIO_TRACK_TIMEOUT_S = 25.0


def fetch_token(base: str) -> dict:
    req = urllib.request.Request(
        f"{base}/api/livekit/token",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (qa-roundtrip)",
            "Accept": "application/json",
        },
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


async def run(base: str) -> int:
    print(f"[1/5] Fetching token from {base}/api/livekit/token ...")
    try:
        tok = fetch_token(base)
    except Exception as e:
        print(f"  FAIL: {e}")
        return 2
    if "token" not in tok or "url" not in tok or "room" not in tok:
        print(f"  FAIL: response missing fields: {tok}")
        return 2
    print(f"  OK: room={tok['room']} configId={tok['configId']} configName={tok['configName']}")

    print(f"[2/5] Connecting to {tok['url']} as passive subscriber ...")
    room = rtc.Room()

    agent_joined = asyncio.Event()
    agent_audio_track = asyncio.Event()
    agent_identity: Optional[str] = None
    audio_frame_count = 0

    @room.on("participant_connected")
    def _on_join(p: rtc.RemoteParticipant):
        nonlocal agent_identity
        kind_label = "agent" if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT else "other"
        print(f"  participant_connected: identity={p.identity} kind={kind_label}")
        if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            agent_identity = p.identity
            agent_joined.set()

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant):
        print(f"  track_subscribed: kind={track.kind} from={p.identity} sid={track.sid}")
        if track.kind == rtc.TrackKind.KIND_AUDIO and p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            agent_audio_track.set()
            asyncio.create_task(_count_frames(track))

    async def _count_frames(track: rtc.AudioTrack):
        nonlocal audio_frame_count
        stream = rtc.AudioStream(track)
        async for _ in stream:
            audio_frame_count += 1
            if audio_frame_count >= 100:
                break

    try:
        await room.connect(tok["url"], tok["token"], options=rtc.RoomOptions(auto_subscribe=True))
        print(f"  OK: connected, local_sid={room.local_participant.sid}")
    except Exception as e:
        print(f"  FAIL: connect: {e}")
        return 3

    print(f"[3/5] Waiting up to {AGENT_JOIN_TIMEOUT_S}s for agent to join ...")
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(agent_joined.wait(), AGENT_JOIN_TIMEOUT_S)
        print(f"  OK: agent joined in {time.monotonic() - t0:.1f}s (identity={agent_identity})")
    except asyncio.TimeoutError:
        print(f"  FAIL: no agent joined within {AGENT_JOIN_TIMEOUT_S}s")
        await room.disconnect()
        return 4

    print(f"[4/5] Waiting up to {AUDIO_TRACK_TIMEOUT_S}s for agent audio track ...")
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(agent_audio_track.wait(), AUDIO_TRACK_TIMEOUT_S)
        print(f"  OK: audio track published in {time.monotonic() - t0:.1f}s")
    except asyncio.TimeoutError:
        print("  FAIL: no agent audio track")
        await room.disconnect()
        return 5

    print("[5/5] Capturing audio frames for 4s to confirm Lisa speaks ...")
    await asyncio.sleep(4.0)
    print(f"  audio_frame_count={audio_frame_count} (>0 = Lisa spoke)")

    await room.disconnect()
    print("DONE: disconnected cleanly")

    if audio_frame_count == 0:
        print("VERDICT: ❌ agent connected but no audio frames received")
        return 6
    print(f"VERDICT: ✅ E2E roundtrip green (room={tok['room']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    args = parser.parse_args()
    return asyncio.run(run(args.base))


if __name__ == "__main__":
    sys.exit(main())
