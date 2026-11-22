from collections import deque
from dataclasses import dataclass
import threading
import numpy as np
import webrtcvad

from .config import settings


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
        silence_ms: int = settings.vad_silence_ms,
        min_speech_ms: int = settings.vad_min_speech_ms,
        max_utterance_ms: int = settings.vad_max_utterance_ms,
        pre_roll_ms: int = settings.vad_pre_roll_ms,
        post_roll_ms: int = settings.vad_post_roll_ms,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_samples = int(sample_rate * frame_duration_ms / 1000)
        self.frame_bytes = self.frame_samples * 2  # int16

        self.vad = webrtcvad.Vad(aggressiveness)
        self.energy_threshold = energy_threshold

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

        self.mode: str = "vad"  # "vad" or "ptt"
        self.ptt_pressed: bool = False

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
            self.ptt_pressed = False

    def _is_speech_frame(self, frame: bytes) -> bool:
        samples = np.frombuffer(frame, dtype=np.int16)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        if rms < self.energy_threshold:
            return False
        return self.vad.is_speech(frame, self.sample_rate)

    def _collect_utterance(self, trim_trailing_silence: int = 0) -> np.ndarray | None:
        frames = self._speech_frames
        active_count = self._active_speech_frames
        self._speech_frames = []
        self._active_speech_frames = 0

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
                    if self.ptt_pressed:
                        self._speech_frames.append(frame)
                        if len(self._speech_frames) >= self.max_utterance_frames:
                            utterance = self._collect_utterance()
                            if utterance is not None:
                                results.append(VADResult(utterance=utterance))
                    continue

                is_speech = self._is_speech_frame(frame)

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
                        self._speech_active = False
                        self._silence_count = 0
                        utterance = self._collect_utterance(trim_trailing_silence=trim)
                        results.append(
                            VADResult(
                                utterance=utterance,
                                speech_state_changed=True,
                                speech_active=False,
                            )
                        )

            return results

