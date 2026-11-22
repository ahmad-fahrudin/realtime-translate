import asyncio
from contextlib import asynccontextmanager
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .config import PIPER_VOICE_CATALOG, SUPPORTED_LANGUAGES, settings
from .pipeline import TranslationEngine, TranslationOutput
from .vad import StreamingVAD

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("rt-engine")

engine = TranslationEngine()


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


app = FastAPI(title="Realtime Translate CPU Engine", version="0.1.0", lifespan=lifespan)


class PrepareRequest(BaseModel):
    src_lang: str
    dst_lang: str


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "whisper_model": settings.whisper_model,
        "installed_translation_pairs": engine.translator.installed_pairs(),
        "installed_tts_languages": engine.tts.installed_languages(),
    }


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
    return await asyncio.to_thread(engine.prepare_pair, req.src_lang, req.dst_lang)


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
    active_tasks: set[asyncio.Task] = set()

    def spawn_task(coro) -> None:
        task = asyncio.create_task(coro)
        active_tasks.add(task)
        task.add_done_callback(active_tasks.discard)

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
                        spawn_task(process_and_reply(res.utterance))

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
                        spawn_task(process_and_reply(utterance))

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
        for task in list(active_tasks):
            task.cancel()
