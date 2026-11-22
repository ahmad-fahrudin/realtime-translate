import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import PIPER_VOICE_CATALOG, SUPPORTED_LANGUAGES, WHISPER_MODEL_CATALOG, settings
from .downloader import DownloadCancelled, format_mb
from .pipeline import TranslationEngine, TranslationOutput
from .pulse_audio import PulseCallBridge, list_pulse_devices, setup_virtual_audio
from .vad import StreamingVAD

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("rt-engine")

STATIC_DIR = Path(__file__).resolve().parent / "static"

engine = TranslationEngine()
control_clients: set[WebSocket] = set()
control_lock = asyncio.Lock()


async def broadcast_control(payload: dict[str, Any]) -> None:
    text = json.dumps(payload)
    async with control_lock:
        clients = list(control_clients)
    for ws in clients:
        try:
            await ws.send_text(text)
        except Exception:
            pass


bridge = PulseCallBridge(engine=engine, on_event=broadcast_control)
download_cancels: dict[str, threading.Event] = {}
download_lock = asyncio.Lock()


def _make_progress_callback(kind: str, target: str, loop: asyncio.AbstractEventLoop):
    last_emit = {"t": 0.0, "pct": -1}

    def _emit(payload: dict[str, Any]) -> None:
        if not loop.is_closed():
            asyncio.run_coroutine_threadsafe(broadcast_control(payload), loop)

    def _on_progress(downloaded: int, total: int, label: str) -> None:
        # Support optional "|<overall_pct>" override for multi-file language packages
        override_pct: int | None = None
        clean_label = label
        if "|" in label:
            clean_label, raw_pct = label.rsplit("|", 1)
            if raw_pct.isdigit():
                override_pct = int(raw_pct)

        if override_pct is not None:
            pct = max(0, min(100, override_pct))
        elif total > 0:
            pct = max(0, min(100, int((downloaded / total) * 100)))
        else:
            pct = 0

        now = time.monotonic()
        if pct != last_emit["pct"] and (now - last_emit["t"] >= 0.15 or pct >= 100):
            last_emit["t"] = now
            last_emit["pct"] = pct
            size_info = (
                f"{format_mb(downloaded)} / {format_mb(total)}"
                if total > 0
                else format_mb(downloaded)
            )
            _emit(
                {
                    "event": "download_progress",
                    "kind": kind,
                    "target": target,
                    "status": "downloading",
                    "percent": pct,
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                    "message": f"Mengunduh {clean_label} ({size_info})",
                }
            )

    return _on_progress


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.preload_default_models:
        logger.info("Preloading Whisper (%s) + Argos (id<->en) + Piper (id, en)...", settings.whisper_model)
        try:
            await asyncio.to_thread(engine.preload_defaults)
            logger.info("Default offline models ready.")
        except Exception as exc:
            logger.warning("Preload warning (models can still load on demand): %s", exc)
    yield
    await bridge.stop()


app = FastAPI(title="Realtime Translate CPU Engine", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class PrepareRequest(BaseModel):
    src_lang: str
    dst_lang: str
    bidirectional: bool = False


class WhisperModelRequest(BaseModel):
    model: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "whisper_model": engine.stt.active_model_name,
        "whisper_device": engine.stt.active_device,
        "installed_whisper_models": engine.stt.installed_models(),
        "translator_backend": engine.translator.active_backend,
        "translator_device": engine.translator.active_device,
        "bridge_running": bridge.running,
        "installed_translation_pairs": engine.translator.installed_pairs(),
        "installed_tts_languages": engine.tts.installed_languages(),
    }


@app.get("/models/whisper")
async def get_whisper_models() -> dict[str, object]:
    installed = set(engine.stt.installed_models())
    return {
        "active_model": engine.stt.active_model_name,
        "active_device": engine.stt.active_device,
        "models": [
            {
                "id": item["id"],
                "label": item["label"],
                "installed": item["id"] in installed,
            }
            for item in WHISPER_MODEL_CATALOG
        ],
    }


@app.post("/models/whisper")
async def switch_whisper_model(req: WhisperModelRequest) -> dict[str, object]:
    if bridge.running:
        raise HTTPException(
            status_code=409,
            detail="Hentikan translasi terlebih dahulu sebelum mengganti model Whisper.",
        )
    if "whisper" in download_cancels:
        raise HTTPException(
            status_code=409,
            detail="Unduhan model Whisper sedang berlangsung.",
        )

    loop = asyncio.get_running_loop()
    cancel_ev = threading.Event()
    download_cancels["whisper"] = cancel_ev
    on_progress = _make_progress_callback("whisper", req.model, loop)

    await broadcast_control(
        {
            "event": "download_progress",
            "kind": "whisper",
            "target": req.model,
            "status": "downloading",
            "percent": 0,
            "message": f"Menyiapkan model {req.model}...",
        }
    )

    try:
        res = await asyncio.to_thread(
            engine.stt.switch_model, req.model, cancel_ev, on_progress
        )
    except DownloadCancelled:
        await broadcast_control(
            {
                "event": "download_progress",
                "kind": "whisper",
                "target": req.model,
                "status": "cancelled",
                "percent": 0,
                "message": "Unduhan model dibatalkan.",
            }
        )
        return {"cancelled": True, "active_model": engine.stt.active_model_name}
    except ValueError as exc:
        await broadcast_control(
            {
                "event": "download_progress",
                "kind": "whisper",
                "target": req.model,
                "status": "error",
                "percent": 0,
                "message": str(exc),
            }
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await broadcast_control(
            {
                "event": "download_progress",
                "kind": "whisper",
                "target": req.model,
                "status": "error",
                "percent": 0,
                "message": str(exc),
            }
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        download_cancels.pop("whisper", None)

    await broadcast_control(
        {
            "event": "download_progress",
            "kind": "whisper",
            "target": req.model,
            "status": "done",
            "percent": 100,
            "message": f"Model {res['active_model']} siap.",
        }
    )
    await broadcast_control(
        {
            "event": "whisper_model",
            "active_model": res["active_model"],
            "active_device": res["active_device"],
            "translator_backend": engine.translator.active_backend,
        }
    )
    return res


@app.post("/models/whisper/cancel")
async def cancel_whisper_download() -> dict[str, object]:
    ev = download_cancels.get("whisper")
    if ev is not None:
        ev.set()
        return {"ok": True, "cancelling": True}
    return {"ok": True, "cancelling": False}


@app.get("/languages")
async def languages() -> dict[str, object]:
    items = []
    installed_tts = set(engine.tts.installed_languages())
    for code, meta in SUPPORTED_LANGUAGES.items():
        items.append(
            {
                "code": code,
                "name": meta["name"],
                "native": meta["native"],
                "tts_supported": code in PIPER_VOICE_CATALOG,
                "tts_installed": code in installed_tts,
            }
        )
    return {
        "languages": items,
        "installed_pairs": engine.translator.installed_pairs(),
    }


@app.post("/models/prepare")
async def prepare_models(req: PrepareRequest) -> dict[str, object]:
    if "language" in download_cancels:
        raise HTTPException(
            status_code=409,
            detail="Unduhan paket bahasa sedang berlangsung.",
        )

    target = (
        f"{req.src_lang}<->{req.dst_lang}"
        if req.bidirectional
        else f"{req.src_lang}->{req.dst_lang}"
    )
    loop = asyncio.get_running_loop()
    cancel_ev = threading.Event()
    download_cancels["language"] = cancel_ev
    on_progress = _make_progress_callback("language", target, loop)

    await broadcast_control(
        {
            "event": "download_progress",
            "kind": "language",
            "target": target,
            "status": "downloading",
            "percent": 0,
            "message": f"Menyiapkan paket bahasa ({target})...",
        }
    )

    try:
        res = await asyncio.to_thread(
            engine.prepare_pair,
            req.src_lang,
            req.dst_lang,
            req.bidirectional,
            cancel_ev,
            on_progress,
        )
    except DownloadCancelled:
        await broadcast_control(
            {
                "event": "download_progress",
                "kind": "language",
                "target": target,
                "status": "cancelled",
                "percent": 0,
                "message": "Unduhan paket bahasa dibatalkan.",
            }
        )
        return {"cancelled": True}
    except Exception as exc:
        await broadcast_control(
            {
                "event": "download_progress",
                "kind": "language",
                "target": target,
                "status": "error",
                "percent": 0,
                "message": str(exc),
            }
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        download_cancels.pop("language", None)

    await broadcast_control(
        {
            "event": "download_progress",
            "kind": "language",
            "target": target,
            "status": "done",
            "percent": 100,
            "message": "Paket bahasa siap.",
        }
    )
    return res


@app.post("/models/prepare/cancel")
async def cancel_language_download() -> dict[str, object]:
    ev = download_cancels.get("language")
    if ev is not None:
        ev.set()
        return {"ok": True, "cancelling": True}
    return {"ok": True, "cancelling": False}


@app.get("/audio/devices")
async def audio_devices() -> dict[str, object]:
    try:
        return await asyncio.to_thread(list_pulse_devices)
    except Exception as exc:
        return {"inputs": [], "outputs": [], "error": str(exc)}


@app.post("/audio/setup-virtual")
async def audio_setup_virtual() -> dict[str, object]:
    return await asyncio.to_thread(setup_virtual_audio)


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket) -> None:
    await ws.accept()
    async with control_lock:
        control_clients.add(ws)
    await ws.send_text(json.dumps({"event": "status", "running": bridge.running}))
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            cmd = msg.get("cmd")
            if cmd == "start":
                await bridge.start(msg.get("config", {}))
            elif cmd == "stop":
                await bridge.stop()
            elif cmd == "update_config":
                prev_my = bridge.config.my_lang
                prev_peer = bridge.config.peer_lang
                bridge.update_config(msg.get("config", {}))
                if bridge.running and (
                    bridge.config.my_lang != prev_my or bridge.config.peer_lang != prev_peer
                ):
                    await asyncio.to_thread(
                        engine.prepare_pair, bridge.config.my_lang, bridge.config.peer_lang
                    )
                    await asyncio.to_thread(
                        engine.prepare_pair, bridge.config.peer_lang, bridge.config.my_lang
                    )
            elif cmd == "ptt":
                bridge.set_ptt(bool(msg.get("pressed", False)))
            elif cmd == "test_text":
                await bridge.test_text(
                    str(msg.get("channel", "outgoing")),
                    str(msg.get("text", "")),
                )
    except WebSocketDisconnect:
        pass
    finally:
        async with control_lock:
            control_clients.discard(ws)


async def _send_output(ws: WebSocket, out: TranslationOutput) -> None:
    payload: dict[str, object] = {
        "type": "translation",
        "id": out.utterance_id,
        "channel": out.channel,
        "src_lang": out.src_lang,
        "dst_lang": out.dst_lang,
        "source_text": out.source_text,
        "translated_text": out.translated_text,
        "latency_ms": out.latency_ms,
        "stt_ms": out.stt_ms,
        "mt_ms": out.mt_ms,
        "tts_ms": out.tts_ms,
        "translator_backend": out.translator_backend,
    }
    if out.tts_warning:
        payload["tts_warning"] = out.tts_warning
    await ws.send_text(json.dumps(payload))

    if out.tts_pcm16 and out.tts_sample_rate:
        meta = {
            "type": "audio_meta",
            "id": out.utterance_id,
            "channel": out.channel,
            "sample_rate": out.tts_sample_rate,
            "channels": 1,
            "format": "pcm_s16le",
            "num_bytes": len(out.tts_pcm16),
        }
        await ws.send_text(json.dumps(meta))
        await ws.send_bytes(out.tts_pcm16)


@app.websocket("/ws/translate")
async def ws_translate(ws: WebSocket) -> None:
    await ws.accept()
    channel = ws.query_params.get("channel", "outgoing")
    state = {
        "channel": channel,
        "src_lang": ws.query_params.get("src_lang", "id"),
        "dst_lang": ws.query_params.get("dst_lang", "en"),
        "tts_enabled": True,
        "mode": "vad",
    }
    vad = StreamingVAD()
    send_lock = asyncio.Lock()
    utterance_queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    async def process_and_reply(audio_f32) -> None:
        try:
            out = await asyncio.to_thread(
                engine.process_audio,
                audio_f32,
                state["channel"],
                state["src_lang"],
                state["dst_lang"],
                state["tts_enabled"],
            )
            if out is not None:
                async with send_lock:
                    await _send_output(ws, out)
        except Exception as exc:
            logger.exception("Pipeline error on %s", state["channel"])
            async with send_lock:
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "channel": state["channel"],
                            "message": str(exc),
                        }
                    )
                )

    async def utterance_worker() -> None:
        while True:
            audio_f32 = await utterance_queue.get()
            try:
                await process_and_reply(audio_f32)
            finally:
                utterance_queue.task_done()

    def enqueue_utterance(audio_f32) -> None:
        if utterance_queue.full():
            try:
                utterance_queue.get_nowait()
                utterance_queue.task_done()
            except asyncio.QueueEmpty:
                pass
        utterance_queue.put_nowait(audio_f32)

    worker_task = asyncio.create_task(utterance_worker())

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            if msg.get("bytes") is not None:
                results = vad.feed(msg["bytes"])
                for res in results:
                    if res.speech_state_changed:
                        async with send_lock:
                            await ws.send_text(
                                json.dumps(
                                    {
                                        "type": "vad",
                                        "channel": state["channel"],
                                        "active": res.speech_active,
                                    }
                                )
                            )
                    if res.utterance is not None:
                        enqueue_utterance(res.utterance)

            elif msg.get("text") is not None:
                data = json.loads(msg["text"])
                msg_type = data.get("type")

                if msg_type == "config":
                    if "channel" in data:
                        state["channel"] = data["channel"]
                    if "src_lang" in data:
                        state["src_lang"] = data["src_lang"]
                    if "dst_lang" in data:
                        state["dst_lang"] = data["dst_lang"]
                    if "tts_enabled" in data:
                        state["tts_enabled"] = bool(data["tts_enabled"])
                    if "mode" in data:
                        state["mode"] = data["mode"]
                        vad.set_mode(state["mode"])

                elif msg_type == "ptt_start":
                    vad.set_mode("ptt")
                    vad.set_ptt(True)
                    async with send_lock:
                        await ws.send_text(
                            json.dumps({"type": "vad", "channel": state["channel"], "active": True})
                        )

                elif msg_type == "ptt_stop":
                    utterance = vad.set_ptt(False)
                    async with send_lock:
                        await ws.send_text(
                            json.dumps({"type": "vad", "channel": state["channel"], "active": False})
                        )
                    if utterance is not None:
                        enqueue_utterance(utterance)

                elif msg_type == "test_text":
                    text = str(data.get("text", "")).strip()
                    if text:
                        out = await asyncio.to_thread(
                            engine.process_text,
                            text,
                            state["channel"],
                            state["src_lang"],
                            state["dst_lang"],
                            state["tts_enabled"],
                        )
                        if out is not None:
                            async with send_lock:
                                await _send_output(ws, out)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected (%s)", state["channel"])
    finally:
        worker_task.cancel()
