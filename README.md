# Realtime Translate (2-Way Live Call Translator)

Sistem penerjemah suara dua arah secara *real-time* untuk panggilan **WhatsApp Web/Desktop, Zoom, Google Meet, atau Discord**, berjalan di dalam Docker (Backend AI + PipeWire/PulseAudio Bridge + Web UI).

---

## Arsitektur

```text
[Mikrofon Fisik] ──(PipeWire/Pulse)──► [parec -> VAD -> Whisper -> Argos -> Piper]
                                                                 │
[WA / Zoom Mic: RT_Virtual_Mic] ◄────────────(pacat)─────────────┘ (TTS Outgoing)

[WA / Zoom Spk: RT_Virtual_Speaker] ──(parec)──► [Mixer + VAD -> Whisper -> Argos -> Piper]
                                                         │
[Headphone Fisik] ◄────────────(pacat + Ducking)─────────┘
[Browser UI + Pop-out Subtitle] ◄───(http://localhost:8765)───┘
```

- **Speech-to-Text (STT)**: `faster-whisper` (`small` `int8`, auto-deteksi GPU CUDA dengan fallback CPU)
- **Machine Translation**: `argostranslate` (offline neural MT, mendukung pivot 2-hop via English)
- **Text-to-Speech (TTS)**: `piper-tts` (ONNX; tersedia untuk `id`, `en`, `es`, `fr`, `de`, `zh`, `ar`; untuk `ja` dan `ko` berjalan *subtitle-only*)
- **Audio Routing & Ducking**: `pactl` / `parec` / `pacat` terhubung ke PipeWire/PulseAudio host lewat `/run/user/$UID/pulse`
- **Frontend**: Web UI di `http://localhost:8765` + **Pop-out Subtitle** (*Picture-in-Picture Always-on-Top*)

---

## Cara Menjalankan

### 1. Jalankan Container
```bash
docker compose up -d
```
*(Gunakan `docker compose up -d --build` hanya saat pertama kali membangun image atau setelah mengubah `Dockerfile`/`requirements.txt`. Perubahan di `engine/app/` ter-mount langsung ke `/app/app`.)*

Saat menyala pertama kali, server otomatis memuat model default (`faster-whisper`, `argos id <-> en`, dan `piper id / en`) ke volume Docker `rt_models`.

Cek status engine lewat:
```bash
curl -s http://localhost:8765/health
```

### 2. Buka Web UI & Siapkan Audio Virtual
1. Buka **`http://localhost:8765`** di browser (Chrome/Chromium/Edge direkomendasikan untuk fitur *Always-on-Top Picture-in-Picture*).
2. Klik **`Setup Virtual Audio`** di UI (atau jalankan `./scripts/linux_virtual_audio.sh up` di terminal host) untuk membuat:
   - **`RT_Virtual_Mic`** (sumber mic virtual untuk dipilih di WA/Zoom/Meet)
   - **`RT_Virtual_Speaker`** (speaker virtual untuk dipilih di WA/Zoom/Meet)

### 3. Hubungkan Perangkat di Panel Routing Audio
Pastikan 4 dropdown pada panel **Routing Audio** terisi benar:

| Field di Web UI | Pilih Perangkat | Fungsi |
|---|---|---|
| **Mikrofon Fisik (Input)** | Mic headset/earphone Anda | Menangkap suara Anda untuk diterjemahkan |
| **Virtual Mic Sink (Ke WA/Zoom)** | `RT_Virtual_Mic_Sink` | Mengirim audio TTS terjemahan ke `RT_Virtual_Mic` |
| **Virtual Speaker Monitor (Dari WA/Zoom)** | `Monitor of RT_Virtual_Speaker` | Menyadap suara lawan bicara dari WA/Zoom |
| **Headphone / Speaker Fisik (Output)** | Headphone/earphone Anda | Memutar suara lawan bicara + TTS terjemahan |

> **Penting:** Gunakan **headset/earphone**, bukan speaker laptop. Jika suara output dari speaker masuk kembali ke mikrofon laptop, sistem akan menganggapnya sebagai ucapan baru (feedback/echo).

### 4. Setting di Aplikasi Call (WhatsApp / Zoom / Google Meet / Discord)
Masuk ke pengaturan audio aplikasi panggilan Anda:
- **Microphone**: pilih **`RT_Virtual_Mic`**
- **Speaker / Audio Output**: pilih **`RT_Virtual_Speaker`**

### 5. Mulai Translasi
1. Pilih **Bahasa Saya** (misalnya `Bahasa Indonesia (id)`) dan **Bahasa Lawan Bicara** (misalnya `English (en)`).
2. Jika memakai bahasa selain `id`/`en`, klik **`Unduh Paket Bahasa`** lebih dulu sebelum call dimulai.
3. Klik **`Mulai Translasi`**.

---

## Panduan Semua Menu & Tombol Web UI

### Baris Status (Atas)
- **`Engine: Ready`** *(hijau)*: Koneksi WebSocket ke backend aktif. Merah (`Reconnecting...`) berarti koneksi ke server terputus.
- **`Mic`** *(lampu biru saat aktif)*: Indikator VAD mendeteksi suara Anda dari `Mikrofon Fisik`.
- **`Peer`** *(lampu biru saat aktif)*: Indikator VAD mendeteksi suara lawan bicara dari `Monitor of RT_Virtual_Speaker`.

### Panel Bahasa
- **`Bahasa Saya`**: Bahasa yang Anda ucapkan dan bahasa hasil terjemahan yang masuk ke telinga/subtitle Anda.
- **`⇄`**: Menukar `Bahasa Saya` dan `Bahasa Lawan Bicara` dengan cepat.
- **`Bahasa Lawan Bicara`**: Bahasa yang diucapkan lawan bicara dan bahasa TTS yang dikirim ke mereka.
- **`Unduh Paket Bahasa`**: Mengunduh paket terjemahan Argos dan suara Piper dua arah (`Saya ↔ Lawan Bicara`) sebelum panggilan berjalan.

### Panel Routing Audio
- **`Setup Virtual Audio`**: Membuat/mereset device `RT_Virtual_Mic` dan `RT_Virtual_Speaker` di PipeWire/PulseAudio.
- **`Refresh`**: Memperbarui daftar perangkat audio (klik setelah mencolokkan/menghubungkan headset Bluetooth).

### Panel Kontrol
- **`Mulai Translasi` / `Hentikan Translasi`**: Menyalakan atau mematikan audio bridge dan pipeline AI.
- **Mode Pemicu**:
  - **`Auto VAD`**: Otomatis mendeteksi awal/akhir kalimat dan menerjemahkan saat ada jeda hening (~750 ms).
  - **`Push-to-Talk`**: Memunculkan tombol **`Tahan untuk Bicara`**. Suara Anda hanya direkam selama tombol ditahan dan langsung diterjemahkan saat dilepas (cocok untuk lingkungan bising).
- **`Bypass Mic`**: Meneruskan suara mic fisik Anda langsung ke `RT_Virtual_Mic_Sink` **tanpa diterjemahkan** (untuk bicara normal sementara).
- **`Ducking` (default `18%`)**: Mengatur seberapa kecil volume suara asli lawan bicara saat suara TTS terjemahan sedang berbicara di headphone Anda (`0%` = suara asli dibisukan saat TTS bunyi, `100%` = tidak dikecilkan).
- **`Pop-out Subtitle`**: Membuka jendela kecil mengambang yang menampilkan 3 subtitle terjemahan terakhir di atas jendela aplikasi lain.

### Panel Transkrip Langsung & Uji Coba
- **Feed Transkrip**: Menampilkan riwayat percakapan (`Anda` / `Lawan Bicara`), teks asli (baris atas), hasil terjemahan (baris bawah), dan latensi (`ms`).
- **`Ketik teks uji coba`**: Simulasi terjemahan + TTS tanpa mikrofon:
  - **`Uji Outgoing`**: Menerjemahkan teks dari `Bahasa Saya → Bahasa Lawan Bicara` dan mengirim suaranya ke `RT_Virtual_Mic_Sink`.
  - **`Uji Incoming`**: Menerjemahkan teks dari `Bahasa Lawan Bicara → Bahasa Saya` dan memutar suaranya ke `Headphone / Speaker Fisik` Anda.

---

## Cara Testing Sebelum Call Sungguhan

1. Pastikan headset sudah terpasang dan dipilih di **Mikrofon Fisik** + **Headphone / Speaker Fisik**.
2. Ketik `"Halo, apa kabar?"` di kolom uji coba → klik **`Uji Incoming`** (untuk memastikan suara TTS terdengar di headphone) atau **`Uji Outgoing`** (untuk cek transkrip & TTS keluar).
3. Klik **`Mulai Translasi`**, ucapkan 1 kalimat yang jelas ke mikrofon (misalnya `"Saya sedang mencoba penerjemah suara"`), lalu beri jeda ~1 detik.
4. Perhatikan lampu **`Mic`** menyala biru saat bicara, lalu mati saat jeda, dan transkrip + terjemahan muncul berurutan.

---

## Konfigurasi Lanjutan (Environment Variables)

Dapat diatur di `docker-compose.yml` dengan prefix `RT_`:

| Variabel | Default | Keterangan |
|---|---|---|
| `RT_WHISPER_MODEL` | `small` | Ukuran model Whisper (`tiny`, `base`, `small`, `medium`, `large-v3`). `small` di GPU memberikan akurasi baik dan respons ~0,5 dtk. |
| `RT_WHISPER_DEVICE` | `auto` | `auto` otomatis memakai `cuda` bila tersedia, lalu fallback ke `cpu`. |
| `RT_WHISPER_COMPUTE_TYPE` | `int8` | Presisi komputasi CTranslate2 (`int8`, `int8_float32`, `float32`). |
| `RT_WHISPER_CPU_THREADS` | `4` | Jumlah thread CPU bila berjalan di mode CPU. |
| `RT_VAD_SILENCE_MS` | `750` | Durasi jeda hening (ms) sebelum kalimat dianggap selesai. |
| `RT_VAD_MIN_SPEECH_MS` | `240` | Durasi minimal suara aktif (ms) agar bunyi klik/napas tidak diproses Whisper. |
| `RT_VAD_ENERGY_THRESHOLD` | `350.0` | Ambang RMS PCM16 minimal untuk dianggap suara bicara. |

---

## Troubleshooting

- **Suara terjemahan tidak nyambung / salah dengar**:
  - Pastikan `RT_WHISPER_MODEL=small` (atau lebih tinggi jika GPU mencukupi) dan `whisper_device` di `/health` bernilai `"cuda"`.
  - Hindari mencampur terlalu banyak kata asing/slang dalam satu kalimat karena penerjemah offline Argos menerjemahkan paling akurat pada kalimat berbahasa Indonesia/Inggris yang utuh.
  - Jika ruangan berisik, gunakan mode **`Push-to-Talk`** agar suara latar tidak memicu transkripsi.
- **Terjadi gema / kalimat berulang sendiri**:
  - Pastikan memakai headset/earphone, dan pastikan **Mikrofon Fisik** tidak sama dengan **Virtual Speaker Monitor**.
- **Perangkat virtual tidak muncul**:
  - Klik **`Setup Virtual Audio`** lalu **`Refresh`**, atau jalankan `./scripts/linux_virtual_audio.sh up` di terminal host.
- **Menjalankan unit test**:
  ```bash
  docker exec realtime-translate python3 -m pytest -v
  ```
