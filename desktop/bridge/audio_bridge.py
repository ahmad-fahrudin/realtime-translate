"""Host Audio Bridge for Realtime Translate.

Runs natively on Linux/Windows/macOS to capture/play audio through physical
and virtual devices, performs real-time audio ducking for incoming calls, and
streams PCM16 16kHz audio over WebSockets to the Dockerized translation engine.
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import math
import queue
import sys
import threading
from typing import Any

import numpy as np
from scipy.signal import resample_poly
import sounddevice as sd
import websockets

ENGINE_SAMPLE_RATE = 16000
BLOCK_MS = 20
CAPTURE_BLOCK_FRAMES_16K = int(ENGINE_SAMPLE_RATE * BLOCK_MS / 1000)


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def resample_stream_chunk(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Fast linear resampling for continuous 20ms streaming capture chunks."""
    if src_sr == dst_sr or data.size == 0:
        return data
    out_len = max(1, int(round(len(data) * dst_sr / src_sr)))
    x_old = np.linspace(0.0, 1.0, len(data), endpoint=False)
    x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
    return np.interp(x_new, x_old, data).astype(np.float32)


def resample_utterance(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """High-quality polyphase resampling for complete TTS utterances."""
    if src_sr == dst_sr or data.size == 0:
        return data
    g = math.gcd(int(src_sr), int(dst_sr))
    up = int(dst_sr) // g
    down = int(src_sr) // g
    return resample_poly(data, up, down).astype(np.float32)


def list_audio_devices() -> dict[str, list[dict[str, Any]]]:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    for idx, dev in enumerate(devices):
        api_name = hostapis[dev["hostapi"]]["name"]
        item = {
            "id": idx,
            "name": dev["name"],
            "hostapi": api_name,
            "default_samplerate": int(dev["default_samplerate"]),
            "max_input_channels": int(dev["max_input_channels"]),
            "max_output_channels": int(dev["max_output_channels"]),
        }
        if dev["max_input_channels"] > 0:
            inputs.append(item)
        if dev["max_output_channels"] > 0:
            outputs.append(item)
    return {"inputs": inputs, "outputs": outputs}


class AudioRingBuffer:
    """Thread-safe float32 mono ring buffer for real-time audio callbacks."""

    def __init__(self, max_samples: int = 48000 * 15) -> None:
        self._buf: deque[np.ndarray] = deque()
        self._len = 0
        self._max_samples = max_samples
        self._lock = threading.Lock()

    def push(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        flat = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        with self._lock:
            self._buf.append(flat)
            self._len += len(flat)
            while self._len > self._max_samples and self._buf:
                dropped = self._buf.popleft()
                self._len -= len(dropped)

    def pull(self, count: int) -> tuple[np.ndarray, bool]:
        """Returns (samples_of_len_count, had_audio)."""
        out = np.zeros(count, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < count and self._buf:
                head = self._buf[0]
                needed = count - filled
                if len(head) <= needed:
                    out[filled : filled + len(head)] = head
                    filled += len(head)
                    self._len -= len(head)
                    self._buf.popleft()
                else:
                    out[filled:] = head[:needed]
                    self._buf[0] = head[needed:]
                    self._len -= needed
                    filled += needed
        return out, filled > 0

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._len = 0


class ChannelSocketClient:
    """Manages one bidirectional WebSocket stream (outgoing or incoming) to Docker engine."""

    def __init__(
        self,
        channel: str,
        engine_url: str,
        src_lang: str,
        dst_lang: str,
        mode: str,
        on_tts_audio,
    ) -> None:
        self.channel = channel
        self.engine_url = engine_url
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.mode = mode
        self.tts_enabled = True
        self.on_tts_audio = on_tts_audio

        self.send_queue: asyncio.Queue[bytes | str] = asyncio.Queue(maxsize=200)
        self._ws: Any = None
        self._stop = False
        self._pending_audio_meta: dict[str, Any] | None = None

    def enqueue_pcm16(self, pcm16: bytes) -> None:
        try:
            self.send_queue.put_nowait(pcm16)
        except asyncio.QueueFull:
            pass

    def enqueue_json(self, payload: dict[str, Any]) -> None:
        try:
            self.send_queue.put_nowait(json.dumps(payload))
        except asyncio.QueueFull:
            pass

    def update_config(self, src_lang: str, dst_lang: str, mode: str, tts_enabled: bool = True) -> None:
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.mode = mode
        self.tts_enabled = tts_enabled
        self.enqueue_json(
            {
                "type": "config",
                "channel": self.channel,
                "src_lang": src_lang,
                "dst_lang": dst_lang,
                "mode": mode,
                "tts_enabled": tts_enabled,
            }
        )

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        url = f"{self.engine_url}?channel={self.channel}&src_lang={self.src_lang}&dst_lang={self.dst_lang}"
        while not self._stop:
            try:
                async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
                    self._ws = ws
                    emit({"event": "ws_status", "channel": self.channel, "connected": True})
                    await ws.send(
                        json.dumps(
                            {
                                "type": "config",
                                "channel": self.channel,
                                "src_lang": self.src_lang,
                                "dst_lang": self.dst_lang,
                                "mode": self.mode,
                                "tts_enabled": self.tts_enabled,
                            }
                        )
                    )
                    sender = asyncio.create_task(self._sender_loop(ws))
                    receiver = asyncio.create_task(self._receiver_loop(ws))
                    done, pending = await asyncio.wait(
                        {sender, receiver},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        exc = task.exception()
                        if exc:
                            raise exc
            except Exception as exc:
                if self._stop:
                    break
                emit({"event": "ws_status", "channel": self.channel, "connected": False})
                emit({"event": "error", "message": f"[{self.channel}] Engine connection retry: {exc}"})
                await asyncio.sleep(1.5)

    async def _sender_loop(self, ws: Any) -> None:
        while not self._stop:
            item = await self.send_queue.get()
            if isinstance(item, bytes):
                await ws.send(item)
            else:
                await ws.send(item)

    async def _receiver_loop(self, ws: Any) -> None:
        async for message in ws:
            if isinstance(message, str):
                data = json.loads(message)
                mtype = data.get("type")
                if mtype == "audio_meta":
                    self._pending_audio_meta = data
                elif mtype in ("vad", "translation", "error"):
                    emit({"event": mtype, **data})
            elif isinstance(message, bytes):
                meta = self._pending_audio_meta or {"sample_rate": 22050}
                self._pending_audio_meta = None
                pcm = np.frombuffer(message, dtype=np.int16).astype(np.float32) / 32768.0
                self.on_tts_audio(pcm, int(meta.get("sample_rate", 22050)))


class BridgeSession:
    """Runs dual-channel sounddevice streams + real-time audio ducking mixer."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.loop: asyncio.AbstractEventLoop | None = None
        self.running = False

        self.bypass: bool = bool(config.get("bypass", False))
        self.ducking_level: float = float(config.get("ducking_level", 0.18))
        self.my_lang: str = str(config.get("my_lang", "id"))
        self.peer_lang: str = str(config.get("peer_lang", "en"))
        self.mode: str = str(config.get("mode", "vad"))
        self.engine_url: str = str(config.get("engine_url", "ws://localhost:8765/ws/translate"))

        self.virtual_mic_buf = AudioRingBuffer()
        self.raw_peer_buf = AudioRingBuffer()
        self.tts_incoming_buf = AudioRingBuffer()

        self._duck_gain = 1.0
        self._streams: list[sd.Stream | sd.InputStream | sd.OutputStream] = []

        self.vmic_sr = self._device_sr(config.get("virtual_mic_sink_id"), output=True)
        self.headphone_sr = self._device_sr(config.get("headphone_id"), output=True)
        self.mic_sr = self._device_sr(config.get("mic_id"), output=False)
        self.vspeaker_sr = self._device_sr(config.get("virtual_speaker_monitor_id"), output=False)

        self.out_client = ChannelSocketClient(
            channel="outgoing",
            engine_url=self.engine_url,
            src_lang=self.my_lang,
            dst_lang=self.peer_lang,
            mode=self.mode,
            on_tts_audio=self._on_outgoing_tts,
        )
        self.in_client = ChannelSocketClient(
            channel="incoming",
            engine_url=self.engine_url,
            src_lang=self.peer_lang,
            dst_lang=self.my_lang,
            mode="vad",
            on_tts_audio=self._on_incoming_tts,
        )

    @staticmethod
    def _device_sr(dev_id: Any, output: bool) -> int:
        if dev_id is None or dev_id == "":
            return 48000
        info = sd.query_devices(int(dev_id))
        return int(info.get("default_samplerate", 48000))

    def _on_outgoing_tts(self, pcm_f32: np.ndarray, src_sr: int) -> None:
        resampled = resample_utterance(pcm_f32, src_sr, self.vmic_sr)
        self.virtual_mic_buf.push(resampled)

    def _on_incoming_tts(self, pcm_f32: np.ndarray, src_sr: int) -> None:
        resampled = resample_utterance(pcm_f32, src_sr, self.headphone_sr)
        self.tts_incoming_buf.push(resampled)

    def update_config(self, patch: dict[str, Any]) -> None:
        if "bypass" in patch:
            self.bypass = bool(patch["bypass"])
        if "ducking_level" in patch:
            self.ducking_level = max(0.0, min(1.0, float(patch["ducking_level"])))
        if "my_lang" in patch:
            self.my_lang = str(patch["my_lang"])
        if "peer_lang" in patch:
            self.peer_lang = str(patch["peer_lang"])
        if "mode" in patch:
            self.mode = str(patch["mode"])

        if self.loop:
            self.loop.call_soon_threadsafe(
                self.out_client.update_config, self.my_lang, self.peer_lang, self.mode, True
            )
            self.loop.call_soon_threadsafe(
                self.in_client.update_config, self.peer_lang, self.my_lang, "vad", True
            )

    def set_ptt(self, pressed: bool) -> None:
        if self.loop:
            msg = {"type": "ptt_start" if pressed else "ptt_stop"}
            self.loop.call_soon_threadsafe(self.out_client.enqueue_json, msg)

    def send_test_text(self, channel: str, text: str) -> None:
        if not self.loop:
            return
        client = self.out_client if channel == "outgoing" else self.in_client
        self.loop.call_soon_threadsafe(client.enqueue_json, {"type": "test_text", "text": text})

    def _mic_callback(self, indata: np.ndarray, _frames: int, _time_info: Any, status: Any) -> None:
        if status:
            pass
        mono = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)
        if self.bypass:
            to_vmic = resample_stream_chunk(mono, self.mic_sr, self.vmic_sr)
            self.virtual_mic_buf.push(to_vmic)
            return

        to_engine = resample_stream_chunk(mono, self.mic_sr, ENGINE_SAMPLE_RATE)
        pcm16 = np.clip(to_engine * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        if self.loop:
            self.loop.call_soon_threadsafe(self.out_client.enqueue_pcm16, pcm16)

    def _vspeaker_callback(self, indata: np.ndarray, _frames: int, _time_info: Any, _status: Any) -> None:
        mono = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)
        to_hp = resample_stream_chunk(mono, self.vspeaker_sr, self.headphone_sr)
        self.raw_peer_buf.push(to_hp)

        if not self.bypass and self.loop:
            to_engine = resample_stream_chunk(mono, self.vspeaker_sr, ENGINE_SAMPLE_RATE)
            pcm16 = np.clip(to_engine * 32767.0, -32768, 32767).astype(np.int16).tobytes()
            self.loop.call_soon_threadsafe(self.in_client.enqueue_pcm16, pcm16)

    def _vmic_out_callback(self, outdata: np.ndarray, frames: int, _time_info: Any, _status: Any) -> None:
        samples, _ = self.virtual_mic_buf.pull(frames)
        outdata[:, 0] = samples
        if outdata.shape[1] > 1:
            outdata[:, 1:] = samples[:, None]

    def _headphone_out_callback(self, outdata: np.ndarray, frames: int, _time_info: Any, _status: Any) -> None:
        raw_chunk, _ = self.raw_peer_buf.pull(frames)
        tts_chunk, has_tts = self.tts_incoming_buf.pull(frames)

        target_gain = self.ducking_level if has_tts else 1.0
        # Smooth attack (fast duck) / release (gentle restore)
        alpha = 0.25 if has_tts else 0.03
        self._duck_gain = (1.0 - alpha) * self._duck_gain + alpha * target_gain

        mixed = np.clip(raw_chunk * self._duck_gain + tts_chunk, -1.0, 1.0)
        outdata[:, 0] = mixed
        if outdata.shape[1] > 1:
            outdata[:, 1:] = mixed[:, None]

    def start_streams(self) -> None:
        mic_id = self.config.get("mic_id")
        vmic_sink_id = self.config.get("virtual_mic_sink_id")
        vspeaker_mon_id = self.config.get("virtual_speaker_monitor_id")
        headphone_id = self.config.get("headphone_id")

        if mic_id not in (None, ""):
            s_mic = sd.InputStream(
                device=int(mic_id),
                channels=1,
                samplerate=self.mic_sr,
                blocksize=int(self.mic_sr * BLOCK_MS / 1000),
                dtype="float32",
                callback=self._mic_callback,
            )
            self._streams.append(s_mic)

        if vmic_sink_id not in (None, ""):
            s_vmic = sd.OutputStream(
                device=int(vmic_sink_id),
                channels=1,
                samplerate=self.vmic_sr,
                blocksize=int(self.vmic_sr * BLOCK_MS / 1000),
                dtype="float32",
                callback=self._vmic_out_callback,
            )
            self._streams.append(s_vmic)

        if vspeaker_mon_id not in (None, ""):
            s_vspk = sd.InputStream(
                device=int(vspeaker_mon_id),
                channels=1,
                samplerate=self.vspeaker_sr,
                blocksize=int(self.vspeaker_sr * BLOCK_MS / 1000),
                dtype="float32",
                callback=self._vspeaker_callback,
            )
            self._streams.append(s_vspk)

        if headphone_id not in (None, ""):
            s_hp = sd.OutputStream(
                device=int(headphone_id),
                channels=1,
                samplerate=self.headphone_sr,
                blocksize=int(self.headphone_sr * BLOCK_MS / 1000),
                dtype="float32",
                callback=self._headphone_out_callback,
            )
            self._streams.append(s_hp)

        for stream in self._streams:
            stream.start()
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.out_client.stop()
        self.in_client.stop()
        for stream in self._streams:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._streams.clear()

    async def run_async(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.start_streams()
        emit({"event": "status", "running": True})
        try:
            await asyncio.gather(self.out_client.run(), self.in_client.run())
        finally:
            self.stop()
            emit({"event": "status", "running": False})


def main() -> None:
    cmd_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def stdin_reader() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                cmd_queue.put(json.loads(line))
            except json.JSONDecodeError as exc:
                emit({"event": "error", "message": f"Invalid JSON command: {exc}"})
        cmd_queue.put({"cmd": "_eof"})

    threading.Thread(target=stdin_reader, daemon=True).start()

    session: BridgeSession | None = None
    session_thread: threading.Thread | None = None

    while True:
        msg = cmd_queue.get()
        cmd = msg.get("cmd")

        if cmd == "_eof":
            if session:
                session.stop()
            break

        elif cmd == "list_devices":
            emit({"event": "devices", **list_audio_devices()})

        elif cmd == "start":
            if session:
                session.stop()
                if session_thread:
                    session_thread.join(timeout=2.0)
            session = BridgeSession(msg.get("config", {}))

            def runner(s: BridgeSession) -> None:
                try:
                    asyncio.run(s.run_async())
                except Exception as exc:
                    emit({"event": "error", "message": str(exc)})
                    emit({"event": "status", "running": False})

            session_thread = threading.Thread(target=runner, args=(session,), daemon=True)
            session_thread.start()

        elif cmd == "update_config" and session:
            session.update_config(msg.get("config", {}))

        elif cmd == "ptt" and session:
            session.set_ptt(bool(msg.get("pressed", False)))

        elif cmd == "test_text" and session:
            session.send_test_text(str(msg.get("channel", "outgoing")), str(msg.get("text", "")))

        elif cmd == "stop":
            if session:
                session.stop()
                if session_thread:
                    session_thread.join(timeout=2.0)
                session = None
            emit({"event": "status", "running": False})


if __name__ == "__main__":
    main()

