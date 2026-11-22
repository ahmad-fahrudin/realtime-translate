const FALLBACK_LANGUAGES = [
  { code: "id", name: "Indonesian", native: "Bahasa Indonesia" },
  { code: "en", name: "English", native: "English" },
  { code: "es", name: "Spanish", native: "Español" },
  { code: "fr", name: "French", native: "Français" },
  { code: "de", name: "German", native: "Deutsch" },
  { code: "zh", name: "Chinese", native: "中文" },
  { code: "ar", name: "Arabic", native: "العربية" },
  { code: "ja", name: "Japanese", native: "日本語" },
  { code: "ko", name: "Korean", native: "한국어" },
];

const el = (id) => document.getElementById(id);

let isRunning = false;
let overlayVisible = false;

function populateLanguages(items) {
  const langs = items && items.length ? items : FALLBACK_LANGUAGES;
  const myLang = el("myLang");
  const peerLang = el("peerLang");
  const prevMy = myLang.value || "id";
  const prevPeer = peerLang.value || "en";

  myLang.innerHTML = "";
  peerLang.innerHTML = "";

  for (const lang of langs) {
    const label = `${lang.native} (${lang.code})`;
    myLang.add(new Option(label, lang.code));
    peerLang.add(new Option(label, lang.code));
  }
  myLang.value = prevMy;
  peerLang.value = prevPeer;
}

function populateDeviceSelect(selectEl, devices, preferredMatchers = []) {
  const prev = selectEl.value;
  selectEl.innerHTML = "";
  selectEl.add(new Option("-- Tidak dipilih --", ""));
  let autoPick = "";

  for (const d of devices) {
    const label = `[${d.hostapi}] ${d.name}`;
    selectEl.add(new Option(label, String(d.id)));
    if (!autoPick && preferredMatchers.some((rx) => rx.test(d.name))) {
      autoPick = String(d.id);
    }
  }
  if (prev && [...selectEl.options].some((o) => o.value === prev)) {
    selectEl.value = prev;
  } else if (autoPick) {
    selectEl.value = autoPick;
  }
}

function getCurrentConfig() {
  return {
    mic_id: el("micSelect").value,
    virtual_mic_sink_id: el("vmicSelect").value,
    virtual_speaker_monitor_id: el("vspkSelect").value,
    headphone_id: el("hpSelect").value,
    my_lang: el("myLang").value,
    peer_lang: el("peerLang").value,
    mode: el("modeSelect").value,
    bypass: el("bypassChk").checked,
    ducking_level: Number(el("duckSlider").value) / 100,
    engine_url: "ws://localhost:8765/ws/translate",
  };
}

function appendFeed(item) {
  const feed = el("feed");
  const div = document.createElement("div");
  div.className = "feed-item";
  const tag = item.channel === "outgoing" ? "Anda" : "Lawan Bicara";
  const warn = item.tts_warning ? ` · ${item.tts_warning}` : "";
  div.innerHTML = `
    <div class="feed-meta">
      <span>${tag} (${item.src_lang} → ${item.dst_lang})</span>
      <span>${item.latency_ms} ms${warn}</span>
    </div>
    <div class="feed-src"></div>
    <div class="feed-dst"></div>
  `;
  div.querySelector(".feed-src").textContent = item.source_text;
  div.querySelector(".feed-dst").textContent = item.translated_text;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

async function refreshEngineHealth() {
  const res = await window.rtApi.getEngineHealth();
  const dot = el("engineDot");
  const label = el("engineStatus");
  if (res.status === "ok") {
    dot.className = "dot ok";
    label.textContent = `Engine: Ready (${res.whisper_model})`;
  } else {
    dot.className = "dot err";
    label.textContent = "Engine: Offline";
  }
}

async function init() {
  populateLanguages(FALLBACK_LANGUAGES);
  const langRes = await window.rtApi.getLanguages();
  if (langRes && langRes.languages && langRes.languages.length) {
    populateLanguages(langRes.languages);
  }
  await refreshEngineHealth();
  await window.rtApi.sendBridgeCmd({ cmd: "list_devices" });
  setInterval(refreshEngineHealth, 6000);
}

window.rtApi.onBridgeEvent((msg) => {
  if (msg.event === "devices") {
    populateDeviceSelect(el("micSelect"), msg.inputs, [/default|pipewire|pulse|headset|mic/i]);
    populateDeviceSelect(el("vmicSelect"), msg.outputs, [/RT_Virtual_Mic_Sink|CABLE Input|BlackHole/i]);
    populateDeviceSelect(el("vspkSelect"), msg.inputs, [/Monitor of RT_Virtual_Speaker|RT_Virtual_Speaker|CABLE Output|BlackHole/i]);
    populateDeviceSelect(el("hpSelect"), msg.outputs, [/default|pipewire|pulse|headphone|analog/i]);
  } else if (msg.event === "status") {
    isRunning = Boolean(msg.running);
    const btn = el("startBtn");
    btn.textContent = isRunning ? "Hentikan Translasi" : "Mulai Translasi";
    btn.className = isRunning ? "danger" : "primary";
  } else if (msg.event === "vad") {
    const dot = msg.channel === "outgoing" ? el("micVadDot") : el("peerVadDot");
    dot.className = msg.active ? "dot active" : "dot";
  } else if (msg.event === "translation") {
    appendFeed(msg);
  } else if (msg.event === "error") {
    el("hintMsg").textContent = msg.message;
  }
});

el("refreshDevBtn").addEventListener("click", () => {
  window.rtApi.sendBridgeCmd({ cmd: "list_devices" });
});

el("linuxVirtualBtn").addEventListener("click", async () => {
  el("hintMsg").textContent = "Menyiapkan Virtual Audio...";
  const res = await window.rtApi.setupLinuxVirtualAudio();
  el("hintMsg").textContent = res.ok ? "Virtual Audio aktif" : res.message;
});

el("swapLangBtn").addEventListener("click", () => {
  const a = el("myLang").value;
  el("myLang").value = el("peerLang").value;
  el("peerLang").value = a;
  if (isRunning) {
    window.rtApi.sendBridgeCmd({ cmd: "update_config", config: getCurrentConfig() });
  }
});

el("prepareBtn").addEventListener("click", async () => {
  el("hintMsg").textContent = "Mengunduh paket bahasa...";
  try {
    await window.rtApi.prepareModels(el("myLang").value, el("peerLang").value);
    await window.rtApi.prepareModels(el("peerLang").value, el("myLang").value);
    el("hintMsg").textContent = "Paket bahasa siap.";
  } catch (err) {
    el("hintMsg").textContent = `Gagal unduh: ${err.message}`;
  }
});

el("startBtn").addEventListener("click", () => {
  if (isRunning) {
    window.rtApi.sendBridgeCmd({ cmd: "stop" });
  } else {
    el("hintMsg").textContent = "";
    window.rtApi.sendBridgeCmd({ cmd: "start", config: getCurrentConfig() });
  }
});

el("modeSelect").addEventListener("change", () => {
  const isPtt = el("modeSelect").value === "ptt";
  el("pttBtn").hidden = !isPtt;
  if (isRunning) {
    window.rtApi.sendBridgeCmd({ cmd: "update_config", config: getCurrentConfig() });
  }
});

el("pttBtn").addEventListener("mousedown", () => window.rtApi.sendBridgeCmd({ cmd: "ptt", pressed: true }));
el("pttBtn").addEventListener("mouseup", () => window.rtApi.sendBridgeCmd({ cmd: "ptt", pressed: false }));

el("bypassChk").addEventListener("change", () => {
  if (isRunning) {
    window.rtApi.sendBridgeCmd({ cmd: "update_config", config: getCurrentConfig() });
  }
});

el("duckSlider").addEventListener("input", () => {
  el("duckVal").textContent = `${el("duckSlider").value}%`;
  if (isRunning) {
    window.rtApi.sendBridgeCmd({ cmd: "update_config", config: getCurrentConfig() });
  }
});

["myLang", "peerLang"].forEach((id) => {
  el(id).addEventListener("change", () => {
    if (isRunning) {
      window.rtApi.sendBridgeCmd({ cmd: "update_config", config: getCurrentConfig() });
    }
  });
});

el("overlayBtn").addEventListener("click", async () => {
  overlayVisible = await window.rtApi.toggleOverlay(!overlayVisible);
});

el("testOutBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) window.rtApi.sendBridgeCmd({ cmd: "test_text", channel: "outgoing", text });
});

el("testInBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) window.rtApi.sendBridgeCmd({ cmd: "test_text", channel: "incoming", text });
});

init();

