"""Host PipeWire/PulseAudio bridge running inside Docker via PULSE_SERVER."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import logging
import subprocess
import threading
import time
from typing import Any, Awaitable, Callable

import numpy as np

from .pipeline import TranslationEngine, TranslationOutput
from .vad import StreamingVAD

logger = logging.getLogger("rt-pulse")

ENGINE_SR = 16000
VMIC_SR = 22050
MIXER_SR = 48000
BLOCK_MS = 20
MIXER_BLOCK_SAMPLES = int(MIXER_SR * BLOCK_MS / 1000)  # 960 samples @ 48kHz
VMIC_BLOCK_SAMPLES = int(VMIC_SR * BLOCK_MS / 1000)  # 441 samples @ 22.05kHz
CAPTURE_16K_BYTES = int(ENGINE_SR * BLOCK_MS / 1000) * 2  # 640 bytes @ 16kHz
CAPTURE_48K_BYTES = MIXER_BLOCK_SAMPLES * 2  # 1920 bytes @ 48kHz
MAX_UTTERANCE_QUEUE = 3


def resample_linear(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or samples.size == 0:
        return samples
    out_len = max(1, int(round(len(samples) * dst_sr / src_sr)))
    x_old = np.linspace(0.0, 1.0, len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def _run_pactl(args: list[str]) -> str:
    res = subprocess.run(["pactl", *args], capture_output=True, text=True, check=True)
    return res.stdout


def list_pulse_devices() -> dict[str, list[dict[str, str]]]:
    """Lists PulseAudio/PipeWire sources (inputs) and sinks (outputs)."""
    inputs: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []

    try:
        raw_sources = json.loads(_run_pactl(["-f", "json", "list", "sources"]))
        for s in raw_sources:
            name = s.get("name", "")
            desc = s.get("description") or s.get("properties", {}).get("device.description") or name
            if name:
                inputs.append({"id": name, "name": desc})

        raw_sinks = json.loads(_run_pactl(["-f", "json", "list", "sinks"]))
        for s in raw_sinks:
            name = s.get("name", "")
            desc = s.get("description") or s.get("properties", {}).get("device.description") or name
            if name:
                outputs.append({"id": name, "name": desc})
        return {"inputs": inputs, "outputs": outputs}
    except Exception:
        # Fallback for older pactl without -f json
        for line in _run_pactl(["list", "short", "sources"]).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                inputs.append({"id": parts[1], "name": parts[1]})
        for line in _run_pactl(["list", "short", "sinks"]).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                outputs.append({"id": parts[1], "name": parts[1]})
        return {"inputs": inputs, "outputs": outputs}


def setup_virtual_audio() -> dict[str, Any]:
    """Creates RT_Virtual_Mic and RT_Virtual_Speaker on host PipeWire/PulseAudio."""
    try:
        modules = _run_pactl(["list", "short", "modules"]).splitlines()
        for line in modules:
            if "RT_Virtual_Mic" in line or "RT_Virtual_Speaker" in line:
                mod_id = line.split("\t")[0].strip()
                if mod_id:
                    subprocess.run(["pactl", "unload-module", mod_id], capture_output=True)

        _run_pactl([
            "load-module",
            "module-null-sink",
            "sink_name=RT_Virtual_Mic_Sink",
            'sink_properties=device.description="RT_Virtual_Mic_Sink"',
        ])
        _run_pactl([
            "load-module",
            "module-remap-source",
            "master=RT_Virtual_Mic_Sink.monitor",
            "source_name=RT_Virtual_Mic",
            'source_properties=device.description="RT_Virtual_Mic"',
        ])
        _run_pactl([
            "load-module",
            "module-null-sink",
            "sink_name=RT_Virtual_Speaker_Sink",
            'sink_properties=device.description="RT_Virtual_Speaker"',
        ])
        return {"ok": True, "devices": list_pulse_devices()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class AudioRingBuffer:
    def __init__(self, max_samples: int = MIXER_SR * 15) -> None:
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


@dataclass
class BridgeConfig:
    mic_source: str = ""
    vmic_sink: str = "RT_Virtual_Mic_Sink"
    vspeaker_monitor: str = "RT_Virtual_Speaker_Sink.monitor"
    headphone_sink: str = ""
    my_lang: str = "id"
    peer_lang: str = "en"
    mode: str = "vad"
    bypass: bool = False
    ducking_level: float = 0.18


class PulseCallBridge:
    """Runs 2-way live call audio routing + ducking inside Docker via parec/pacat."""

    def __init__(
        self,
        engine: TranslationEngine,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.engine = engine
        self.on_event = on_event
        self.config = BridgeConfig()
        self.running = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = threading.Event()
        self._procs: list[subprocess.Popen[bytes]] = []
        self._threads: list[threading.Thread] = []
        self._worker_tasks: list[asyncio.Task[Any]] = []
        self._channel_queues: dict[str, asyncio.Queue[np.ndarray]] = {}

        self._out_vad = StreamingVAD()
        self._in_vad = StreamingVAD()

        self._raw_peer_buf = AudioRingBuffer(max_samples=int(MIXER_SR * 0.25))
        self._tts_in_buf = AudioRingBuffer()
        self._tts_out_buf = AudioRingBuffer(max_samples=VMIC_SR * 15)
        self._bypass_out_buf = AudioRingBuffer(max_samples=int(VMIC_SR * 0.25))
        self._duck_gain = 1.0

    def _emit_threadsafe(self, payload: dict[str, Any]) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.on_event(payload), self._loop)

    def _enqueue_utterance_threadsafe(self, audio_f32: np.ndarray, channel: str) -> None:
        if not self._loop or self._loop.is_closed():
            return

        def _put() -> None:
            q = self._channel_queues.get(channel)
            if q is None:
                return
            while q.qsize() >= MAX_UTTERANCE_QUEUE:
                try:
                    q.get_nowait()
                    logger.warning("Dropped stale queued utterance on %s to preserve sync", channel)
                except asyncio.QueueEmpty:
                    break
            q.put_nowait(audio_f32)

        self._loop.call_soon_threadsafe(_put)

    def update_config(self, patch: dict[str, Any]) -> None:
        if "mic_source" in patch:
            self.config.mic_source = str(patch["mic_source"])
        if "vmic_sink" in patch:
            self.config.vmic_sink = str(patch["vmic_sink"])
        if "vspeaker_monitor" in patch:
            self.config.vspeaker_monitor = str(patch["vspeaker_monitor"])
        if "headphone_sink" in patch:
            self.config.headphone_sink = str(patch["headphone_sink"])
        if "my_lang" in patch:
            self.config.my_lang = str(patch["my_lang"])
        if "peer_lang" in patch:
            self.config.peer_lang = str(patch["peer_lang"])
        if "mode" in patch:
            self.config.mode = str(patch["mode"])
            self._out_vad.set_mode(self.config.mode)
        if "bypass" in patch:
            new_bypass = bool(patch["bypass"])
            if new_bypass != self.config.bypass:
                self.config.bypass = new_bypass
                self._out_vad.reset()
                self._out_vad.set_mode(self.config.mode)
                self._tts_out_buf.clear()
                self._bypass_out_buf.clear()
        if "ducking_level" in patch:
            self.config.ducking_level = max(0.0, min(1.0, float(patch["ducking_level"])))

    def set_ptt(self, pressed: bool) -> None:
        if not self.running:
            return
        self._out_vad.set_mode("ptt")
        utterance = self._out_vad.set_ptt(pressed)
        self._emit_threadsafe({"event": "vad", "channel": "outgoing", "active": pressed})
        if utterance is not None:
            self._enqueue_utterance_threadsafe(utterance, "outgoing")

    async def test_text(self, channel: str, text: str) -> None:
        src = self.config.my_lang if channel == "outgoing" else self.config.peer_lang
        dst = self.config.peer_lang if channel == "outgoing" else self.config.my_lang
        out = await asyncio.to_thread(self.engine.process_text, text, channel, src, dst, True)
        if out is not None:
            await self._deliver_output(out)

    async def start(self, config_dict: dict[str, Any]) -> None:
        async with self._lifecycle_lock:
            await self._stop_unlocked()
            self.update_config(config_dict)
            self._loop = asyncio.get_running_loop()
            self._stop_event.clear()
            self._out_vad.reset()
            self._out_vad.set_mode(self.config.mode)
            self._in_vad.reset()
            self._raw_peer_buf.clear()
            self._tts_in_buf.clear()
            self._tts_out_buf.clear()
            self._bypass_out_buf.clear()
            self._duck_gain = 1.0

            if (
                self.config.mic_source
                and self.config.vspeaker_monitor
                and self.config.mic_source == self.config.vspeaker_monitor
            ):
                await self.on_event(
                    {
                        "event": "error",
                        "message": "Mikrofon Fisik dan Virtual Speaker Monitor tidak boleh sama.",
                    }
                )
                await self.on_event({"event": "status", "running": False})
                return

            # Warm up selected language pair
            await asyncio.to_thread(
                self.engine.prepare_pair, self.config.my_lang, self.config.peer_lang
            )
            await asyncio.to_thread(
                self.engine.prepare_pair, self.config.peer_lang, self.config.my_lang
            )

            self._channel_queues = {
                "outgoing": asyncio.Queue(),
                "incoming": asyncio.Queue(),
            }
            self._worker_tasks = [
                asyncio.create_task(self._channel_worker("outgoing")),
                asyncio.create_task(self._channel_worker("incoming")),
            ]

            if self.config.vmic_sink:
                t_vmic = threading.Thread(target=self._vmic_writer_loop, daemon=True)
                t_vmic.start()
                self._threads.append(t_vmic)

            if self.config.mic_source:
                t_mic = threading.Thread(target=self._mic_capture_loop, daemon=True)
                t_mic.start()
                self._threads.append(t_mic)

            if self.config.vspeaker_monitor:
                t_vspk = threading.Thread(target=self._vspeaker_capture_loop, daemon=True)
                t_vspk.start()
                self._threads.append(t_vspk)

            if self.config.headphone_sink:
                t_hp = threading.Thread(target=self._headphone_mixer_loop, daemon=True)
                t_hp.start()
                self._threads.append(t_hp)

            self.running = True
            await self.on_event({"event": "status", "running": True})

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        self._stop_event.set()
        self.running = False
        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks.clear()
        self._channel_queues.clear()

        for proc in self._procs:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs.clear()
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        await self.on_event({"event": "status", "running": False})

    async def _channel_worker(self, channel: str) -> None:
        queue = self._channel_queues[channel]
        while not self._stop_event.is_set():
            audio_f32 = await queue.get()
            try:
                await self._process_utterance(audio_f32, channel)
            finally:
                queue.task_done()

    def _vmic_writer_loop(self) -> None:
        proc = subprocess.Popen(
            [
                "pacat",
                f"--device={self.config.vmic_sink}",
                f"--rate={VMIC_SR}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=20",
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(proc)
        assert proc.stdin is not None

        tick = BLOCK_MS / 1000.0
        next_t = time.perf_counter()

        while not self._stop_event.is_set():
            if self.config.bypass:
                chunk, _ = self._bypass_out_buf.pull(VMIC_BLOCK_SAMPLES)
            else:
                chunk, _ = self._tts_out_buf.pull(VMIC_BLOCK_SAMPLES)

            pcm = np.clip(chunk * 32767.0, -32768, 32767).astype(np.int16).tobytes()
            try:
                proc.stdin.write(pcm)
                proc.stdin.flush()
            except Exception:
                break

            next_t += tick
            sleep_for = next_t - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_t = time.perf_counter()

    def _mic_capture_loop(self) -> None:
        proc = subprocess.Popen(
            [
                "parec",
                f"--device={self.config.mic_source}",
                f"--rate={ENGINE_SR}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(proc)
        assert proc.stdout is not None

        while not self._stop_event.is_set():
            chunk = proc.stdout.read(CAPTURE_16K_BYTES)
            if not chunk:
                break

            if self.config.bypass:
                f32 = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                up = resample_linear(f32, ENGINE_SR, VMIC_SR)
                self._bypass_out_buf.push(up)
                continue

            results = self._out_vad.feed(chunk)
            for res in results:
                if res.speech_state_changed:
                    self._emit_threadsafe(
                        {"event": "vad", "channel": "outgoing", "active": res.speech_active}
                    )
                if res.utterance is not None:
                    self._enqueue_utterance_threadsafe(res.utterance, "outgoing")

    def _vspeaker_capture_loop(self) -> None:
        proc = subprocess.Popen(
            [
                "parec",
                f"--device={self.config.vspeaker_monitor}",
                f"--rate={MIXER_SR}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(proc)
        assert proc.stdout is not None

        while not self._stop_event.is_set():
            chunk = proc.stdout.read(CAPTURE_48K_BYTES)
            if not chunk:
                break

            f32_48k = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            self._raw_peer_buf.push(f32_48k)

            if self.config.bypass:
                continue

            # Exact 3x decimation 48kHz -> 16kHz
            trim = (len(f32_48k) // 3) * 3
            if trim == 0:
                continue
            f32_16k = f32_48k[:trim].reshape(-1, 3).mean(axis=1)
            pcm16_16k = np.clip(f32_16k * 32767.0, -32768, 32767).astype(np.int16).tobytes()

            results = self._in_vad.feed(pcm16_16k)
            for res in results:
                if res.speech_state_changed:
                    self._emit_threadsafe(
                        {"event": "vad", "channel": "incoming", "active": res.speech_active}
                    )
                if res.utterance is not None:
                    self._enqueue_utterance_threadsafe(res.utterance, "incoming")

    def _headphone_mixer_loop(self) -> None:
        proc = subprocess.Popen(
            [
                "pacat",
                f"--device={self.config.headphone_sink}",
                f"--rate={MIXER_SR}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=20",
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(proc)
        assert proc.stdin is not None

        tick = BLOCK_MS / 1000.0
        next_t = time.perf_counter()

        while not self._stop_event.is_set():
            raw_chunk, _ = self._raw_peer_buf.pull(MIXER_BLOCK_SAMPLES)
            tts_chunk, has_tts = self._tts_in_buf.pull(MIXER_BLOCK_SAMPLES)

            target_gain = self.config.ducking_level if has_tts else 1.0
            alpha = 0.25 if has_tts else 0.03
            self._duck_gain = (1.0 - alpha) * self._duck_gain + alpha * target_gain

            mixed = np.clip(raw_chunk * self._duck_gain + tts_chunk, -1.0, 1.0)
            pcm = (mixed * 32767.0).astype(np.int16).tobytes()
            try:
                proc.stdin.write(pcm)
                proc.stdin.flush()
            except Exception:
                break

            next_t += tick
            sleep_for = next_t - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_t = time.perf_counter()

    async def _process_utterance(self, audio_f32: np.ndarray, channel: str) -> None:
        src = self.config.my_lang if channel == "outgoing" else self.config.peer_lang
        dst = self.config.peer_lang if channel == "outgoing" else self.config.my_lang
        try:
            out = await asyncio.to_thread(
                self.engine.process_audio, audio_f32, channel, src, dst, True
            )
            if out is not None:
                await self._deliver_output(out)
        except Exception as exc:
            logger.exception("Bridge pipeline error (%s)", channel)
            await self.on_event({"event": "error", "message": str(exc)})

    async def _deliver_output(self, out: TranslationOutput) -> None:
        payload: dict[str, Any] = {
            "event": "translation",
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
        await self.on_event(payload)

        if out.tts_pcm16 and out.tts_sample_rate:
            f32 = np.frombuffer(out.tts_pcm16, dtype=np.int16).astype(np.float32) / 32768.0
            if out.channel == "outgoing":
                resampled = resample_linear(f32, out.tts_sample_rate, VMIC_SR)
                self._tts_out_buf.push(resampled)
            else:
                resampled = resample_linear(f32, out.tts_sample_rate, MIXER_SR)
                self._tts_in_buf.push(resampled)

