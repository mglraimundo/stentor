import asyncio
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import uuid
import wave

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

# Configuration
APP_NAME = os.getenv("APP_NAME", "Área A")
FAVICON_LETTER = os.getenv("FAVICON_LETTER", "S")
FAVICON_BG_COLOR = os.getenv("FAVICON_BG_COLOR", "#2563EB")
FAVICON_TEXT_COLOR = os.getenv("FAVICON_TEXT_COLOR", "#FFFFFF")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
MAX_RECORDING_SECONDS = int(os.getenv("MAX_RECORDING_SECONDS", "20"))
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "")
VOLUME_BOOST = os.getenv("VOLUME_BOOST", "1.0")
DRY_RUN = os.getenv("DRY_RUN", "0") in ("1", "true", "True", "yes")
NORMALIZE_VOLUME = os.getenv("NORMALIZE_VOLUME", "0") in ("1", "true", "True", "yes")
QUEUE_GAP_SECONDS = 2
MAX_QUEUE_SIZE = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("stentor")

# --- State ---
audio_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
connected_clients: dict[str, WebSocket] = {}
audio_temp_dir: str = ""
ding_file_path: str = ""


# --- Ding generation ---

def generate_ding_wav(path: str) -> None:
    """Generate a two-tone chime WAV file (G4 then D4)."""
    sample_rate = 48000
    duration = 1.0
    length = int(sample_rate * duration)

    # First pass: collect raw samples
    samples = []
    for i in range(length):
        t = i / sample_rate
        val = 0.0
        # Tone 1: G4 (392 Hz), 0-0.55s
        if t < 0.55:
            att = 1 - math.exp(-t * 20)
            dec = math.exp(-t * 3)
            fade = 0.5 * (1 + math.cos(math.pi * (t - 0.4) / 0.15)) if t > 0.4 else 1.0
            val += att * dec * fade * math.sin(2 * math.pi * 392 * t)
        # Tone 2: D4 (294 Hz), 0.4-1.0s
        if t >= 0.4:
            t2 = t - 0.4
            att = 1 - math.exp(-t2 * 20)
            dec = math.exp(-t2 * 2.5)
            fade = 0.5 * (1 + math.cos(math.pi * (t - 0.85) / 0.15)) if t > 0.85 else 1.0
            val += att * dec * fade * math.sin(2 * math.pi * 294 * t2)
        samples.append(val)

    # Second pass: peak-normalize to -3 dBFS so the ding is consistently loud
    # regardless of synthesis amplitude; headroom left for the playback limiter
    peak = max(abs(v) for v in samples)
    target_peak = 10 ** (-3 / 20)  # ≈ 0.708
    scale = target_peak / peak if peak > 0 else 1.0

    raw = bytearray()
    for val in samples:
        s = max(-1.0, min(1.0, val * scale))
        raw.extend(struct.pack("<h", int(s * 32767)))

    # 100ms of silence so the audio driver can flush cleanly
    raw.extend(b"\x00\x00" * int(sample_rate * 0.10))

    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(raw))


# --- Audio playback ---

def play_audio_file(filepath: str) -> subprocess.Popen | None:
    """Spawn ffplay to play an audio file. Returns the process."""
    if DRY_RUN:
        logger.info("DRY_RUN: would play %s", filepath)
        return None
    try:
        env = os.environ.copy()
        if AUDIO_DEVICE:
            env["AUDIODEV"] = AUDIO_DEVICE
        return subprocess.Popen(
            [
                "ffplay", "-nodisp", "-autoexit",
                "-af", f"highpass=f=80,"
                       f"acompressor=threshold=-18dB:ratio=3:attack=5:release=100:makeup=2dB,"
                       f"volume={VOLUME_BOOST},"
                       f"alimiter=limit=1:attack=5:release=50,"
                       f"aformat=channel_layouts=stereo",
                filepath,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        logger.error("ffplay not found. Install ffmpeg: sudo apt install ffmpeg")
        return None


async def play_and_wait(filepath: str) -> None:
    """Play an audio file and wait for playback to finish."""
    proc = play_audio_file(filepath)
    if proc:
        await asyncio.to_thread(proc.wait)


async def normalize_audio(input_path: str) -> str | None:
    """Normalize audio loudness using EBU R128 two-pass loudnorm. Returns path or None."""
    # Pass 1: measure loudness stats
    proc1 = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", input_path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-vn", "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr1 = await proc1.communicate()
    if proc1.returncode != 0:
        logger.warning("loudnorm pass 1 failed (exit %d)", proc1.returncode)
        return None

    match = re.search(r'\{[^{}]+\}', stderr1.decode(), re.DOTALL)
    if not match:
        logger.warning("loudnorm pass 1: could not parse JSON stats")
        return None
    try:
        stats = json.loads(match.group())
    except json.JSONDecodeError:
        logger.warning("loudnorm pass 1: JSON decode error")
        return None

    # Pass 2: apply normalization using measured values for accuracy
    output_path = input_path + ".norm.webm"
    af = (
        f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true"
        f":measured_I={stats['input_i']}"
        f":measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}"
        f":measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
    )
    proc2 = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", input_path,
        "-af", af,
        "-c:a", "libopus", "-b:a", "96k",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr2 = await proc2.communicate()
    if proc2.returncode == 0:
        return output_path
    logger.warning(
        "loudnorm pass 2 failed (exit %d): %s",
        proc2.returncode, stderr2.decode()[-200:],
    )
    try:
        os.unlink(output_path)
    except OSError:
        pass
    return None


async def process_queue() -> None:
    """Sequentially play queued audio messages: ding -> message -> gap."""
    while True:
        filepath, client_id = await audio_queue.get()
        normalized_path = None
        try:
            logger.info(
                "Playing message from %s (%d queued)",
                client_id, audio_queue.qsize(),
            )

            if NORMALIZE_VOLUME:
                normalized_path = await normalize_audio(filepath)
                if normalized_path:
                    logger.info("Normalized audio for %s", client_id)

            play_path = normalized_path or filepath

            if ding_file_path:
                await play_and_wait(ding_file_path)

            await play_and_wait(play_path)

        except Exception as e:
            logger.error("Error playing audio: %s", e)
        finally:
            for p in (filepath, normalized_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        if not audio_queue.empty():
            await asyncio.sleep(QUEUE_GAP_SECONDS)


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    global audio_temp_dir, ding_file_path

    audio_temp_dir = tempfile.mkdtemp(prefix="stentor_")

    ding_file_path = os.path.join(audio_temp_dir, "ding.wav")
    generate_ding_wav(ding_file_path)
    logger.info("Ding sound ready: %s", ding_file_path)

    task = asyncio.create_task(process_queue())
    logger.info("Queue processor started")

    yield

    task.cancel()
    shutil.rmtree(audio_temp_dir, ignore_errors=True)


app = FastAPI(lifespan=lifespan)


# --- Endpoints ---

@app.get("/config")
async def get_config():
    return JSONResponse({
        "app_name": APP_NAME,
        "favicon_letter": FAVICON_LETTER,
        "favicon_bg_color": FAVICON_BG_COLOR,
        "favicon_text_color": FAVICON_TEXT_COLOR,
        "max_recording_seconds": MAX_RECORDING_SECONDS,
    })


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_id = str(uuid.uuid4())[:8]
    connected_clients[client_id] = ws
    logger.info("Client connected: %s (total: %d)", client_id, len(connected_clients))

    await ws.send_text(json.dumps({
        "type": "welcome",
        "client_id": client_id,
    }))

    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.receive":
                if "bytes" in message:
                    audio_data = message["bytes"]
                    if DRY_RUN:
                        logger.info(
                            "DRY_RUN: received %d bytes from %s",
                            len(audio_data), client_id,
                        )
                    else:
                        if audio_queue.full():
                            await ws.send_text(json.dumps({
                                "type": "queue_full",
                            }))
                            logger.warning(
                                "Queue full, rejected message from %s",
                                client_id,
                            )
                        else:
                            fd, filepath = tempfile.mkstemp(
                                suffix=".webm", dir=audio_temp_dir,
                            )
                            with os.fdopen(fd, "wb") as f:
                                f.write(audio_data)
                            await audio_queue.put((filepath, client_id))
                            position = audio_queue.qsize()
                            await ws.send_text(json.dumps({
                                "type": "queued",
                                "position": position,
                            }))
                            logger.info(
                                "Queued message from %s (%d bytes, queue: %d)",
                                client_id, len(audio_data), position,
                            )

            elif message["type"] == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.pop(client_id, None)
        logger.info(
            "Client disconnected: %s (total: %d)",
            client_id, len(connected_clients),
        )


# Mount static files last so /config and /ws take precedence
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
