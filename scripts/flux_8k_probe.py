#!/usr/bin/env python3
"""Flux 8 kHz acceptance probe — merge-gate C1 (see docs/validate-flux-8khz.md).

Streams an 8 kHz mono PCM16 WAV to Deepgram Flux (v2) and prints raw responses.
  PASS  = ">>> SOCKET OPENED" + transcript/turn messages below it.
  FAIL  = connect error / immediate close mentioning sample rate (8 kHz rejected).

Verify the query params against the live docs before trusting a FAIL:
  https://developers.deepgram.com/docs/flux/quickstart
  https://developers.deepgram.com/docs/flux/configuration

Requires: `websockets` (already pulled in by pipecat-ai[websocket]; else `pip install websockets`)
          and DEEPGRAM_API_KEY in the environment.

Usage:    python scripts/flux_8k_probe.py sample_8k.wav
"""
import asyncio
import os
import sys
import wave

import websockets

# To test the 16 kHz fallback (Step 6), change sample_rate=8000 -> 16000 and pass sample_16k.wav.
URL = (
    "wss://api.deepgram.com/v2/listen"
    "?model=flux-general-multi"
    "&encoding=linear16"
    "&sample_rate=8000"
    "&eot_threshold=0.7"
)


def open_wav(path: str) -> wave.Wave_read:
    wf = wave.open(path, "rb")
    fmt = (wf.getframerate(), wf.getsampwidth(), wf.getnchannels())
    if fmt != (8000, 2, 1):
        sys.exit(
            f"FAIL(input): need 8 kHz mono PCM16, got "
            f"{fmt[0]} Hz / {fmt[1] * 8}-bit / {fmt[2]}ch. "
            f"Convert with: ffmpeg -i in.wav -ar 8000 -ac 1 -c:a pcm_s16le sample_8k.wav"
        )
    return wf


async def _connect(key: str):
    headers = {"Authorization": f"Token {key}"}
    try:  # websockets >= 12
        return await websockets.connect(URL, additional_headers=headers)
    except TypeError:  # websockets < 12
        return await websockets.connect(URL, extra_headers=headers)


async def main() -> None:
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        sys.exit("FAIL: DEEPGRAM_API_KEY not set")
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/flux_8k_probe.py sample_8k.wav")
    wf = open_wav(sys.argv[1])

    try:
        ws = await _connect(key)
    except Exception as e:  # connect refused = the C1 failure mode
        sys.exit(f"FAIL(connect): Flux refused the 8 kHz connection -> {e!r}\n"
                 f"If this is a sample-rate error, use the 16 kHz fallback (runbook Step 6).")

    print(">>> SOCKET OPENED at 8 kHz")
    received = 0

    async def send() -> None:
        while chunk := wf.readframes(1600):  # ~0.2 s @ 8 kHz
            await ws.send(chunk)
            await asyncio.sleep(0.18)  # roughly real-time pacing

    async def recv() -> None:
        nonlocal received
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=6)
                received += 1
                print("RECV:", str(msg)[:600])
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass

    await asyncio.gather(send(), recv())
    await ws.close()
    print(
        f">>> DONE: {received} message(s) received. "
        f"PASS if you see transcript/turn data above. "
        f"0 messages or an error frame -> re-check params vs the Flux quickstart before concluding FAIL."
    )


if __name__ == "__main__":
    asyncio.run(main())
