# Realtime Translate (2-Way Live Call Translator)

Sistem penerjemah suara dua arah secara *real-time* untuk panggilan **WhatsApp Desktop/Web, Zoom, Google Meet, atau Discord**, berjalan lokal di CPU menggunakan **Docker** (tanpa LLM berbayar dan tanpa GPU) + **Desktop App (Electron + Audio Bridge)**.

## Arsitektur

```
[Mikrofon Fisik] ──► Audio Bridge ──► WS (outgoing) ──► Docker Engine (VAD -> Faster-Whisper -> Argos -> Piper)
                                                               │
[WA / Zoom Mic: RT_Virtual_Mic] ◄──────────────────────────────┘ (TTS Audio)

[WA / Zoom Spk: RT_Virtual_Speaker] ──► Audio Bridge ──► WS (incoming) ──► Docker Engine
                                              │                                  │
                                              ▼                                  ▼
                                    [Audio Ducking Mixer] ◄──────────────────────┘ (TTS Audio)
                                              │
                                              ▼
                                      [Headphone Fisik] + [Floating Subtitle Overlay]
```

- **Docker Engine (`engine/`)**: FastAPI + WebSocket server menjalankan `webrtcvad` -> `faster-whisper` (CPU `int8`) -> `argostranslate` (Neural MT offline) -> `piper-tts` (ONNX CPU).
- **Host Desktop (`desktop/`)**: Electron Control Panel + Floating Always-on-Top Subtitle Overlay + `audio_bridge.py` (`sounddevice` + `scipy`) untuk menangkap/memutar audio host secara lintas platform dan melakukan *audio ducking*.

---

## 1. Setup Virtual Audio (Sekali di Awal)

### Linux (PipeWire / PulseAudio)
Jalankan helper script yang sudah disediakan (atau klik tombol **Setup Virtual Audio (Linux)** di aplikasi):

```bash
./scripts/linux_virtual_audio.sh up
```

Ini membuat 2 perangkat virtual:
- `RT_Virtual_Mic` (pilih sebagai **Microphone** di WA Web / Zoom / Meet)
- `RT_Virtual_Speaker` (pilih sebagai **Speaker** di WA Web / Zoom / Meet)

Untuk menghapusnya kembali:
```bash
./scripts/linux_virtual_audio.sh down
```

### Windows
1. Install **VB-Cable** (gratis) dan **Virtual Audio Cable** (atau Voicemeeter Banana untuk 2 virtual cable terpisah).
2. Di WA/Zoom/Meet: pilih **CABLE Input** / Virtual Mic sebagai Microphone, dan jalur virtual kedua sebagai Speaker.

### macOS
1. Install **BlackHole 2ch** (untuk mic virtual) + **BlackHole 16ch** / virtual device kedua (untuk speaker virtual).
2. Pilih masing-masing jalur di pengaturan Audio WA/Zoom/Meet dan di Desktop App.

---

## 2. Menjalankan Engine di Docker (Lokal, CPU-only)

```bash
docker compose up -d --build
```

Saat pertama kali menyala, container akan mengunduh model default (`faster-whisper tiny`, `argos id <-> en`, dan `piper id / en`) ke volume Docker `rt_models`.
Cek status engine:

```bash
curl http://localhost:8765/health
```

---

## 3. Menjalankan Desktop App + Host Audio Bridge

Siapkan virtual environment Python untuk Audio Bridge di host (cukup sekali):

```bash
cd desktop/bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ../..
```

Install dependensi Electron & jalankan aplikasi:

```bash
cd desktop
npm install
npm start
```

### Cara Pakai Saat Panggilan
1. Pastikan container Docker (`localhost:8765`) berstatus **Ready**.
2. Pilih pasangan bahasa (misal `Bahasa Indonesia (id)` ⇄ `English (en)`).
3. Pilih 4 perangkat audio di Control Panel:
   - **Mikrofon Fisik**: mic asli Anda
   - **Virtual Mic Sink**: `RT_Virtual_Mic_Sink`
   - **Virtual Speaker Monitor**: `Monitor of RT_Virtual_Speaker`
   - **Headphone Fisik**: headset/earphone Anda *(wajib pakai headset agar tidak terjadi feedback)*
4. Di WhatsApp Web / Desktop / Zoom: set **Microphone = `RT_Virtual_Mic`** dan **Speaker = `RT_Virtual_Speaker`**.
5. Klik **Mulai Translasi** dan aktifkan **Subtitle Overlay** jika ingin subtitle melayang di layar.

