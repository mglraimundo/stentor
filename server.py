import asyncio
import json
import logging
import os
import subprocess
import time
import uuid

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
BROADCAST_TIMEOUT_SECONDS = int(os.getenv("BROADCAST_TIMEOUT_SECONDS", "20"))
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "")
VOLUME_BOOST = os.getenv("VOLUME_BOOST", "1.0")
DRY_RUN = os.getenv("DRY_RUN", "0") in ("1", "true", "True", "yes")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("stentor")

app = FastAPI()

# --- Broadcast state ---

active_broadcaster: str | None = None
broadcast_start_time: float | None = None
timeout_task: asyncio.Task | None = None
ffplay_process: subprocess.Popen | None = None
connected_clients: dict[str, WebSocket] = {}


def start_ffplay() -> subprocess.Popen | None:
    if DRY_RUN:
        logger.info("DRY_RUN: skipping ffplay")
        return None
    try:
        env = os.environ.copy()
        if AUDIO_DEVICE:
            env["AUDIODEV"] = AUDIO_DEVICE
        proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit",
             "-fflags", "nobuffer", "-analyzeduration", "0", "-probesize", "32",
             "-af", f"volume={VOLUME_BOOST},alimiter=limit=1:attack=5:release=50,aformat=channel_layouts=stereo",
             "-i", "pipe:0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        logger.info("ffplay started (pid %d)", proc.pid)
        return proc
    except FileNotFoundError:
        logger.error("ffplay not found. Install ffmpeg: sudo apt install ffmpeg")
        return None


def stop_ffplay() -> None:
    global ffplay_process
    if ffplay_process is not None:
        try:
            ffplay_process.stdin.close()
        except Exception:
            pass
        try:
            ffplay_process.terminate()
            ffplay_process.wait(timeout=2)
        except Exception:
            try:
                ffplay_process.kill()
            except Exception:
                pass
        logger.info("ffplay stopped")
        ffplay_process = None


async def broadcast_to_all(message: dict) -> None:
    payload = json.dumps(message)
    disconnected = []
    for cid, ws in connected_clients.items():
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.append(cid)
    for cid in disconnected:
        connected_clients.pop(cid, None)


def get_seconds_remaining() -> int | None:
    if active_broadcaster is None or broadcast_start_time is None:
        return None
    elapsed = time.monotonic() - broadcast_start_time
    remaining = max(0, BROADCAST_TIMEOUT_SECONDS - int(elapsed))
    return remaining


async def send_state_update() -> None:
    await broadcast_to_all({
        "type": "state_update",
        "is_active": active_broadcaster is not None,
        "active_client": active_broadcaster,
        "seconds_remaining": get_seconds_remaining(),
    })


async def end_broadcast(reason: str) -> None:
    global active_broadcaster, broadcast_start_time, timeout_task
    was_active = active_broadcaster
    active_broadcaster = None
    broadcast_start_time = None

    if timeout_task is not None:
        timeout_task.cancel()
        timeout_task = None

    stop_ffplay()

    if was_active:
        logger.info("Broadcast ended: %s (client: %s)", reason, was_active)
        await broadcast_to_all({"type": "broadcast_ended", "reason": reason})
        await send_state_update()


async def timeout_countdown(client_id: str) -> None:
    try:
        await asyncio.sleep(BROADCAST_TIMEOUT_SECONDS)
        if active_broadcaster == client_id:
            await end_broadcast("timeout")
    except asyncio.CancelledError:
        pass


async def grant_broadcast(client_id: str) -> None:
    global active_broadcaster, broadcast_start_time, timeout_task, ffplay_process

    active_broadcaster = client_id
    broadcast_start_time = time.monotonic()
    ffplay_process = start_ffplay()
    timeout_task = asyncio.create_task(timeout_countdown(client_id))

    logger.info("Broadcast granted to %s", client_id)
    await broadcast_to_all({"type": "broadcast_granted", "client_id": client_id})
    await send_state_update()


# --- Endpoints ---

@app.get("/config")
async def get_config():
    return JSONResponse({
        "app_name": APP_NAME,
        "favicon_letter": FAVICON_LETTER,
        "favicon_bg_color": FAVICON_BG_COLOR,
        "favicon_text_color": FAVICON_TEXT_COLOR,
        "broadcast_timeout_seconds": BROADCAST_TIMEOUT_SECONDS,
    })


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_id = str(uuid.uuid4())[:8]
    connected_clients[client_id] = ws
    logger.info("Client connected: %s (total: %d)", client_id, len(connected_clients))

    # Send current state to the newly connected client
    await ws.send_text(json.dumps({
        "type": "welcome",
        "client_id": client_id,
    }))
    await ws.send_text(json.dumps({
        "type": "state_update",
        "is_active": active_broadcaster is not None,
        "active_client": active_broadcaster,
        "seconds_remaining": get_seconds_remaining(),
    }))

    try:
        while True:
            message = await ws.receive()

            if message["type"] == "websocket.receive":
                if "text" in message:
                    data = json.loads(message["text"])
                    msg_type = data.get("type")

                    if msg_type == "request_broadcast":
                        if active_broadcaster is None:
                            await grant_broadcast(client_id)
                        else:
                            await ws.send_text(json.dumps({
                                "type": "broadcast_denied",
                                "reason": "another user is broadcasting",
                            }))

                    elif msg_type == "stop_broadcast":
                        if active_broadcaster == client_id:
                            await end_broadcast("user_stopped")

                elif "bytes" in message:
                    # Binary audio data
                    if active_broadcaster == client_id:
                        audio_data = message["bytes"]
                        if DRY_RUN:
                            logger.debug(
                                "DRY_RUN: received %d bytes from %s",
                                len(audio_data), client_id,
                            )
                        elif ffplay_process and ffplay_process.stdin:
                            try:
                                ffplay_process.stdin.write(audio_data)
                                ffplay_process.stdin.flush()
                            except (BrokenPipeError, OSError):
                                logger.warning("ffplay pipe broken, ending broadcast")
                                await end_broadcast("user_stopped")

            elif message["type"] == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.pop(client_id, None)
        logger.info("Client disconnected: %s (total: %d)", client_id, len(connected_clients))
        if active_broadcaster == client_id:
            await end_broadcast("user_stopped")


# Mount static files last so /config and /ws take precedence
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
