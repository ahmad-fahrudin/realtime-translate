from collections import deque
from dataclasses import dataclass
import threading
import time
import numpy as np
import webrtcvad

from ..core.config import settings


@dataclass
class VADResult:
    utterance: np.ndarray | None = None
    speech_state_changed: bool = False
    speech_active: bool = False


class StreamingVAD:
    """Streaming VAD segmenter for 16kHz mono PCM16 audio.

    Combines WebRTC VAD with an RMS energy gate, pre-roll ring buffer,
    silence hangover, and max utterance cap. Also supports Push-to-Talk mode.
    """

    def __init__(
        self,
        sample_rate: int = settings.sample_rate,
        frame_duration_ms: int = settings.frame_duration_ms,
        aggressiveness: int = settings.vad_aggressiveness,
        energy_threshold: float = settings.vad_energy_threshold,
        continue_energy_threshold: float = settings.vad_continue_energy_threshold,
        silence_ms: int = settings.vad_silence_ms,
        min_speech_ms: int = settings.vad_min_speech_ms,
        max_utterance_ms: int = settings.vad_max_utterance_ms,
        pre_roll_ms: int = settings.vad_pre_roll_ms,
        post_roll_ms: int = settings.vad_post_roll_ms,
        enable_highpass: bool = True,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_samples = int(sample_rate * frame_duration_ms / 1000)
        self.frame_bytes = self.frame_samples * 2  # int16

        self.aggressiveness = max(0, min(3, int(aggressiveness)))
        self.vad = webrtcvad.Vad(self.aggressiveness)
        self.energy_threshold = energy_threshold
        self.continue_energy_threshold = min(energy_threshold, continue_energy_threshold)
        self.enable_highpass = enable_highpass
        self._hp_alpha = 0.9695  # ~80 Hz 1st-order high-pass @ 16 kHz
        self._hp_x_prev = 0.0
        self._hp_y_prev = 0.0
        self.last_rms: float = 0.0

        self.silence_frames_limit = max(1, silence_ms // frame_duration_ms)
        self.min_speech_frames = max(1, min_speech_ms // frame_duration_ms)
        self.max_utterance_frames = max(10, max_utterance_ms // frame_duration_ms)
        self.pre_roll_frames = max(1, pre_roll_ms // frame_duration_ms)
        self.post_roll_frames = max(1, post_roll_ms // frame_duration_ms)

        self._lock = threading.RLock()
        self._raw_buffer = bytearray()
        self._pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        self._speech_frames: list[bytes] = []
        self._active_speech_frames = 0
        self._speech_active = False
        self._silence_count = 0
        self._suppress_until = 0.0

        self.mode: str = "vad"  # "vad" or "ptt"
        self.ptt_pressed: bool = False

    def suppress_for(self, seconds: float) -> None:
        """Temporarily raise speech-start energy gate while headphone TTS is playing."""
        deadline = time.monotonic() + max(0.0, seconds)
        if deadline > self._suppress_until:
            self._suppress_until = deadline

    def _apply_highpass(self, frame: bytes) -> bytes:
        if not self.enable_highpass:
            return frame
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return frame
        out = np.empty_like(samples)
        alpha = self._hp_alpha
        x_prev = self._hp_x_prev
        y_prev = self._hp_y_prev
        for i in range(samples.size):
            x = float(samples[i])
            y = alpha * (y_prev + x - x_prev)
            out[i] = y
            x_prev = x
            y_prev = y
        self._hp_x_prev = x_prev
        self._hp_y_prev = y_prev
        return np.clip(out, -32768.0, 32767.0).astype(np.int16).tobytes()

    def _frame_rms(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype=np.int16)
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    def _is_speech_frame(self, frame: bytes, continuing: bool = False) -> bool:
        filtered = self._apply_highpass(frame)
        rms = self._frame_rms(filtered)
        self.last_rms = rms
        if continuing:
            thresh = self.continue_energy_threshold
        else:
            thresh = self.energy_threshold
            if time.monotonic() < self._suppress_until:
                thresh = max(thresh, 700.0)
        if rms < thresh:
            return False
        return self.vad.is_speech(filtered, self.sample_rate)

    def configure(
        self,
        energy_threshold: float | None = None,
        silence_ms: int | None = None,
        aggressiveness: int | None = None,
    ) -> None:
        with self._lock:
            if energy_threshold is not None:
                val = max(80.0, min(2000.0, float(energy_threshold)))
                self.energy_threshold = val
                self.continue_energy_threshold = min(val, max(100.0, round(val * 0.52, 1)))
            if silence_ms is not None:
                ms = max(250, min(3000, int(silence_ms)))
                self.silence_frames_limit = max(1, ms // self.frame_duration_ms)
            if aggressiveness is not None:
                agg = max(0, min(3, int(aggressiveness)))
                if agg != self.aggressiveness:
                    self.aggressiveness = agg
                    self.vad = webrtcvad.Vad(agg)

    def set_mode(self, mode: str) -> None:
        with self._lock:
            if mode in ("vad", "ptt") and mode != self.mode:
                self.mode = mode
                self.reset()

    def set_ptt(self, pressed: bool) -> np.ndarray | None:
        with self._lock:
            self.ptt_pressed = pressed
            if pressed:
                self._speech_frames.clear()
                self._active_speech_frames = 0
                self._speech_active = True
                return None
            return self.flush()

    def reset(self) -> None:
        with self._lock:
            self._raw_buffer.clear()
            self._pre_roll.clear()
            self._speech_frames.clear()
            self._active_speech_frames = 0
            self._speech_active = False
            self._silence_count = 0
            self._hp_x_prev = 0.0
            self._hp_y_prev = 0.0
            self.last_rms = 0.0
            self.ptt_pressed = False

    def _collect_utterance(
        self,
        trim_trailing_silence: int = 0,
        split_on_pause: bool = False,
    ) -> np.ndarray | None:
        frames = self._speech_frames
        active_count = self._active_speech_frames
        remainder: list[bytes] = []

        if self.mode != "ptt" and split_on_pause and len(frames) >= 20:
            # Find the quietest frame in the last ~1.5s to split between words, not mid-word.
            search_len = min(len(frames) // 2, 75)
            start_idx = len(frames) - search_len
            best_idx = min(range(start_idx, len(frames)), key=lambda i: self._frame_rms(frames[i]))
            if best_idx > 0 and best_idx < len(frames) - 1:
                remainder = frames[best_idx + 1 :]
                frames = frames[: best_idx + 1]

        self._speech_frames = remainder
        self._active_speech_frames = (
            sum(1 for f in remainder if self._frame_rms(f) >= self.continue_energy_threshold)
            if remainder
            else 0
        )

        if self.mode == "ptt":
            if len(frames) < self.min_speech_frames:
                return None
        else:
            # Count actual voiced frames (excluding pre-roll and trailing silence)
            # so single-frame clicks or breaths do not trigger Whisper hallucinations.
            if active_count < self.min_speech_frames:
                return None
            if trim_trailing_silence > 0 and len(frames) > trim_trailing_silence:
                frames = frames[:-trim_trailing_silence]

        pcm_bytes = b"".join(frames)
        return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    def flush(self) -> np.ndarray | None:
        with self._lock:
            self._speech_active = False
            self._silence_count = 0
            self._raw_buffer.clear()
            return self._collect_utterance()

    def feed(self, pcm16_bytes: bytes) -> list[VADResult]:
        with self._lock:
            results: list[VADResult] = []
            self._raw_buffer.extend(pcm16_bytes)

            while len(self._raw_buffer) >= self.frame_bytes:
                frame = bytes(self._raw_buffer[: self.frame_bytes])
                del self._raw_buffer[: self.frame_bytes]

                if self.mode == "ptt":
                    self.last_rms = self._frame_rms(self._apply_highpass(frame))
                    if self.ptt_pressed:
                        self._speech_frames.append(frame)
                    continue

                is_speech = self._is_speech_frame(frame, continuing=self._speech_active)

                if not self._speech_active:
                    self._pre_roll.append(frame)
                    if is_speech:
                        self._speech_active = True
                        self._silence_count = 0
                        self._active_speech_frames = 1
                        self._speech_frames.extend(self._pre_roll)
                        self._pre_roll.clear()
                        results.append(VADResult(speech_state_changed=True, speech_active=True))
                else:
                    self._speech_frames.append(frame)
                    if is_speech:
                        self._silence_count = 0
                        self._active_speech_frames += 1
                    else:
                        self._silence_count += 1

                    reached_silence = self._silence_count >= self.silence_frames_limit
                    reached_max = len(self._speech_frames) >= self.max_utterance_frames

                    if reached_silence or reached_max:
                        trim = (
                            max(0, self._silence_count - self.post_roll_frames)
                            if reached_silence
                            else 0
                        )
                        utterance = self._collect_utterance(
                            trim_trailing_silence=trim,
                            split_on_pause=reached_max and not reached_silence,
                        )
                        if reached_max and not reached_silence and self._speech_frames:
                            # Continue recording the remainder smoothly without dropping speech
                            self._speech_active = True
                            self._silence_count = 0
                            if utterance is not None:
                                results.append(VADResult(utterance=utterance))
                        else:
                            self._speech_active = False
                            self._silence_count = 0
                            results.append(
                                VADResult(
                                    utterance=utterance,
                                    speech_state_changed=True,
                                    speech_active=False,
                                )
                            )

            return results

