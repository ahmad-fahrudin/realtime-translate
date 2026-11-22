# Realtime Translate (All-in-Docker 2-Way Live Call Translator)

Sistem penerjemah suara dua arah secara *real-time* untuk panggilan **WhatsApp Web/Desktop, Zoom, Google Meet, atau Discord**, berjalan **100% di dalam Docker** (Backend AI CPU + Audio Bridge + Web UI) tanpa perlu menginstall `venv`, `pip`, atau `npm/Electron` di host Linux Anda.

## Arsitektur

```
[Mikrofon Fisik] ──(PipeWire/Pulse Socket)──► [Docker: parec -> VAD -> Whisper -> Argos -> Piper]
                                                                 │
[WA / Zoom Mic: RT_Virtual_Mic] ◄────────────(pacat)─────────────┘ (TTS Outgoing)

[WA / Zoom Spk: RT_Virtual_Speaker] ──(parec)──► [Docker: Mixer + VAD -> Whisper -> Argos -> Piper]
                                                         │
[Headphone Fisik] ◄────────────(pacat + Ducking)─────────┘
[Browser UI + Pop-out Subtitle] ◄───(http://localhost:8765)───┘
```

- **Speech-to-Text**: `faster-whisper` (`tiny` CPU INT8)
- **Translation**: `argostranslate` (Offline Neural MT, tanpa LLM berbayar)
- **Text-to-Speech**: `piper-tts` (ONNX CPU)
- **Audio Routing & Ducking**: `pactl` / `parec` / `pacat` terhubung langsung ke PipeWire/PulseAudio host melalui `/run/user/1000/pulse/native`
- **Frontend**: Web UI langsung dari FastAPI (`http://localhost:8765`) + **Pop-out Subtitle** (*Picture-in-Picture Always-on-Top*)

---

## Cara Menjalankan (2 Langkah)

### 1. Build & Jalankan Container Docker
```bash
docker compose up -d --build
```

> Catatan: Saat pertama kali menyala, container akan mengunduh model default (`faster-whisper tiny`, `argos id <-> en`, dan `piper id / en`) ke volume Docker `rt_models`.

### 2. Buka Web UI & Setup Audio
1. Buka **`http://localhost:8765`** di browser (Chrome/Chromium/Edge direkomendasikan agar tombol **Pop-out Subtitle** memakai *Always-on-Top Picture-in-Picture*).
2. Klik **Setup Virtual Audio** di UI (atau jalankan `./scripts/linux_virtual_audio.sh up` di terminal) untuk membuat `RT_Virtual_Mic` dan `RT_Virtual_Speaker`.
3. Pilih 4 perangkat audio di panel **Routing Audio**:
   - **Mikrofon Fisik**: mic headset Anda
   - **Virtual Mic Sink**: `RT_Virtual_Mic_Sink`
   - **Virtual Speaker Monitor**: `Monitor of RT_Virtual_Speaker`
   - **Headphone Fisik**: output headset/earphone Anda *(gunakan headset agar tidak terjadi echo)*
4. Di **WhatsApp Web / Desktop / Zoom**: set **Microphone = `RT_Virtual_Mic`** dan **Speaker = `RT_Virtual_Speaker`**.
5. Klik **Mulai Translasi**.
