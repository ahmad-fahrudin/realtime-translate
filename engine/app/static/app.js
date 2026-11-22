const el = (id) => document.getElementById(id);

let ws = null;
let isRunning = false;
let pipWindow = null;
let engineLabel = "Engine: Ready";
let activeWhisperModel = "small";
let activeTranslatorBackend = "nllb";
let voicesByLang = {};
let savedPrefs = {
  my_lang: "id",
  peer_lang: "en",
  outgoing_voice: "auto",
  incoming_voice: "auto",
  mic_source: "",
  vmic_sink: "RT_Virtual_Mic_Sink",
  vspeaker_monitor: "RT_Virtual_Speaker_Sink.monitor",
  headphone_sink: "",
  mode: "vad",
  bypass: false,
  ducking_level: 0.18,
  outgoing_tts_enabled: true,
  incoming_tts_enabled: true,
  tts_speed: 1.0,
  vad_energy_threshold: 350,
  vad_silence_ms: 750,
  peer_auto_detect: false,
  translator_backend: "nllb",
};

function populateVoiceSelect(selectEl, lang, preferredId) {
  if (!selectEl) return;
  const prev = selectEl.value;
  const items = voicesByLang[lang] || [];
  selectEl.innerHTML = "";
  if (items.length === 0) {
    selectEl.add(new Option("Tidak tersedia (Subtitle saja)", "auto"));
    selectEl.value = "auto";
    return;
  }
  for (const v of items) {
    const tag = v.installed ? "Terpasang" : "Perlu Unduh";
    selectEl.add(new Option(`${v.label} [${tag}]`, v.id));
  }
  if (prev && [...selectEl.options].some((o) => o.value === prev)) {
    selectEl.value = prev;
  } else if (preferredId && [...selectEl.options].some((o) => o.value === preferredId)) {
    selectEl.value = preferredId;
  }
}

function refreshVoiceSelects(useSavedPrefs = false) {
  // Outgoing speaks in peerLang; incoming speaks in myLang
  populateVoiceSelect(
    el("outgoingVoiceSelect"),
    el("peerLang").value,
    useSavedPrefs ? savedPrefs.outgoing_voice : null
  );
  populateVoiceSelect(
    el("incomingVoiceSelect"),
    el("myLang").value,
    useSavedPrefs ? savedPrefs.incoming_voice : null
  );
}

function populateWhisperModels(data) {
  const sel = el("whisperModelSelect");
  if (!sel || !data) return;
  const models = data.models || [];
  activeWhisperModel = data.active_model || activeWhisperModel;

  sel.innerHTML = "";
  for (const m of models) {
    const tag = m.installed ? "Terpasang" : "Perlu Unduh";
    sel.add(new Option(`${m.label} [${tag}]`, m.id));
  }
  if ([...sel.options].some((o) => o.value === activeWhisperModel)) {
    sel.value = activeWhisperModel;
  }
  syncWhisperApplyBtn();
}

function syncWhisperApplyBtn() {
  const sel = el("whisperModelSelect");
  const btn = el("applyWhisperBtn");
  if (!sel || !btn) return;
  const same = sel.value === activeWhisperModel;
  btn.textContent = same ? "Aktif" : "Pakai Model";
  btn.disabled = isRunning || same;
}

function populateLanguages(items) {
  const myLang = el("myLang");
  const peerLang = el("peerLang");
  const prevMy = myLang.value || savedPrefs.my_lang || "id";
  const prevPeer = peerLang.value || savedPrefs.peer_lang || "en";

  myLang.innerHTML = "";
  peerLang.innerHTML = "";
  for (const lang of items) {
    const label = `${lang.native} (${lang.code})`;
    myLang.add(new Option(label, lang.code));
    peerLang.add(new Option(label, lang.code));
  }
  if ([...myLang.options].some((o) => o.value === prevMy)) myLang.value = prevMy;
  if ([...peerLang.options].some((o) => o.value === prevPeer)) peerLang.value = prevPeer;
}

function populateDeviceSelect(selectEl, devices, preferredMatchers = [], savedId = "") {
  const prev = selectEl.value || savedId;
  selectEl.innerHTML = "";
  selectEl.add(new Option("-- Tidak dipilih --", ""));
  let autoPick = "";

  for (const d of devices) {
    selectEl.add(new Option(d.name, d.id));
    if (!autoPick && preferredMatchers.some((rx) => rx.test(d.id) || rx.test(d.name))) {
      autoPick = d.id;
    }
  }
  if (prev && [...selectEl.options].some((o) => o.value === prev)) {
    selectEl.value = prev;
  } else if (autoPick) {
    selectEl.value = autoPick;
  } else if (devices.length === 1) {
    selectEl.value = devices[0].id;
  }
}

function applyDevices(devices) {
  const inputs = devices.inputs || [];
  const outputs = devices.outputs || [];

  const physicalMics = inputs.filter(
    (d) => !/\.monitor$/i.test(d.id) && !/^RT_Virtual_/i.test(d.id) && !/^Monitor of /i.test(d.name)
  );
  const virtualMicSinks = outputs.filter(
    (d) => /^RT_Virtual_Mic/i.test(d.id) || /RT_Virtual_Mic/i.test(d.name)
  );
  const virtualSpeakerMonitors = inputs.filter(
    (d) => /^RT_Virtual_Speaker.*\.monitor$/i.test(d.id) || /Monitor of RT_Virtual_Speaker/i.test(d.name)
  );
  const physicalOutputs = outputs.filter(
    (d) => !/^RT_Virtual_/i.test(d.id) && !/RT_Virtual_/i.test(d.name)
  );

  populateDeviceSelect(
    el("micSelect"),
    physicalMics,
    [/bluez_input\./i, /alsa_input\./i, /mic|headset/i],
    savedPrefs.mic_source
  );
  populateDeviceSelect(el("vmicSelect"), virtualMicSinks, [/^RT_Virtual_Mic_Sink$/i], savedPrefs.vmic_sink);
  populateDeviceSelect(
    el("vspkSelect"),
    virtualSpeakerMonitors,
    [/^RT_Virtual_Speaker_Sink\.monitor$/i],
    savedPrefs.vspeaker_monitor
  );
  populateDeviceSelect(
    el("hpSelect"),
    physicalOutputs,
    [/bluez_output\./i, /alsa_output\./i, /analog|headphone|speaker/i],
    savedPrefs.headphone_sink
  );
}

function getConfig() {
  return {
    mic_source: el("micSelect").value,
    vmic_sink: el("vmicSelect").value,
    vspeaker_monitor: el("vspkSelect").value,
    headphone_sink: el("hpSelect").value,
    my_lang: el("myLang").value,
    peer_lang: el("peerLang").value,
    outgoing_voice: el("outgoingVoiceSelect").value,
    incoming_voice: el("incomingVoiceSelect").value,
    mode: el("modeSelect").value,
    bypass: el("bypassChk").checked,
    ducking_level: Number(el("duckSlider").value) / 100,
    outgoing_tts_enabled: el("outgoingTtsChk").checked,
    incoming_tts_enabled: el("incomingTtsChk").checked,
    tts_speed: Number(el("ttsSpeedSlider").value) / 100,
    vad_energy_threshold: Number(el("vadThresholdSlider").value),
    vad_silence_ms: Number(el("vadSilenceSlider").value),
    peer_auto_detect: el("peerAutoLangChk").checked,
    translator_backend: el("translatorBackendSelect").value,
  };
}

function appendFeed(item) {
  const feed = el("feed");
  const div = document.createElement("div");
  div.className = "feed-item";
  const tag = item.channel === "outgoing" ? "Anda" : "Lawan Bicara";
  const warn = item.tts_warning ? ` · ${item.tts_warning}` : "";
  const hasStages = typeof item.mt_ms === "number";
  const timing = hasStages
    ? `${item.latency_ms} ms (STT ${item.stt_ms || 0} · MT ${item.mt_ms} · TTS ${item.tts_ms || 0})`
    : `${item.latency_ms} ms`;
  div.innerHTML = `
    <div class="feed-meta">
      <span>${tag} (${item.src_lang} → ${item.dst_lang})</span>
      <span>${timing}${warn}</span>
    </div>
    <div class="feed-src"></div>
    <div class="feed-dst"></div>
  `;
  div.querySelector(".feed-src").textContent = item.source_text;
  div.querySelector(".feed-dst").textContent = item.translated_text;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;

  if (pipWindow && !pipWindow.closed) {
    const lines = pipWindow.document.getElementById("pipLines");
    if (lines) {
      const line = pipWindow.document.createElement("div");
      line.style.cssText = "display:flex;flex-direction:column;gap:2px;";
      const tagEl = pipWindow.document.createElement("span");
      tagEl.style.cssText = `font-size:11px;font-weight:600;color:${item.channel === "incoming" ? "#22c55e" : "#3b82f6"}`;
      tagEl.textContent = `${tag} (${item.dst_lang.toUpperCase()})`;
      const txtEl = pipWindow.document.createElement("span");
      txtEl.style.cssText = "font-size:15px;font-weight:500;color:#fff;";
      txtEl.textContent = item.translated_text;
      line.appendChild(tagEl);
      line.appendChild(txtEl);
      lines.appendChild(line);
      while (lines.children.length > 3) lines.removeChild(lines.firstChild);
    }
  }
}

function sendWs(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

const LOCKABLE_CONTROL_IDS = [
  "myLang",
  "swapLangBtn",
  "peerLang",
  "outgoingVoiceSelect",
  "incomingVoiceSelect",
  "whisperModelSelect",
  "applyWhisperBtn",
  "prepareBtn",
  "setupVirtualBtn",
  "refreshDevBtn",
  "micSelect",
  "vmicSelect",
  "vspkSelect",
  "hpSelect",
  "modeSelect",
  "outgoingTtsChk",
  "incomingTtsChk",
  "bypassChk",
  "duckSlider",
  "translatorBackendSelect",
  "ttsSpeedSlider",
  "vadThresholdSlider",
  "vadSilenceSlider",
  "peerAutoLangChk",
  "testInput",
  "testOutBtn",
  "testInBtn",
];

function setControlsLocked(locked) {
  for (const id of LOCKABLE_CONTROL_IDS) {
    const node = el(id);
    if (node) node.disabled = locked;
  }
  document.querySelectorAll(".toggle-chip, .inline-toggle").forEach((chip) => {
    chip.classList.toggle("is-disabled", locked);
  });
  if (!locked) {
    syncWhisperApplyBtn();
  }
}

function setRunningState(running) {
  isRunning = running;
  const btn = el("startBtn");
  btn.disabled = false;
  btn.textContent = running ? "Hentikan Translasi" : "Mulai Translasi";
  btn.className = running ? "danger" : "primary";
  setControlsLocked(running);
}

function updateDownloadProgressUI(msg) {
  const isWhisper = msg.kind === "whisper";
  const wrap = el(isWhisper ? "whisperProgressWrap" : "langProgressWrap");
  const bar = el(isWhisper ? "whisperProgressBar" : "langProgressBar");
  const text = el(isWhisper ? "whisperProgressText" : "langProgressText");
  const cancelBtn = el(isWhisper ? "cancelWhisperBtn" : "cancelLangBtn");
  if (!wrap || !bar || !text) return;

  if (msg.status === "downloading") {
    wrap.hidden = false;
    const pct = Math.max(0, Math.min(100, Number(msg.percent) || 0));
    bar.style.width = `${pct}%`;
    text.textContent = `${pct}% · ${msg.message || "Mengunduh..."}`;
    if (cancelBtn) cancelBtn.disabled = false;
  } else {
    wrap.hidden = true;
    bar.style.width = "0%";
    text.textContent = "0%";
  }
}

function connectControlWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/control`);

  ws.onopen = () => {
    el("engineDot").className = "dot ok";
    el("engineStatus").textContent = engineLabel;
  };

  ws.onclose = () => {
    el("engineDot").className = "dot err";
    el("engineStatus").textContent = "Engine: Reconnecting...";
    setRunningState(false);
    setTimeout(connectControlWs, 1500);
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.event === "status") {
      setRunningState(Boolean(msg.running));
    } else if (msg.event === "devices") {
      applyDevices(msg);
    } else if (msg.event === "vad") {
      const dot = msg.channel === "outgoing" ? el("micVadDot") : el("peerVadDot");
      dot.className = msg.active ? "dot active" : "dot";
    } else if (msg.event === "translation") {
      appendFeed(msg);
    } else if (msg.event === "download_progress") {
      updateDownloadProgressUI(msg);
    } else if (msg.event === "whisper_model") {
      activeWhisperModel = msg.active_model || activeWhisperModel;
      const dev = msg.active_device || "cuda";
      const mt = msg.translator_backend || activeTranslatorBackend;
      engineLabel = `Ready · ${activeWhisperModel} (${dev}) · ${mt}`;
      el("engineStatus").textContent = engineLabel;
      fetch("/models/whisper").then((r) => r.json()).then(populateWhisperModels).catch(() => { });
    } else if (msg.event === "error") {
      el("hintMsg").textContent = msg.message;
      if (!isRunning) {
        setRunningState(false);
      }
    }
  };
}

async function openPipOverlay() {
  if (pipWindow && !pipWindow.closed) {
    pipWindow.close();
    pipWindow = null;
    return;
  }

  let win = null;
  if ("documentPictureInPicture" in window) {
    win = await window.documentPictureInPicture.requestWindow({ width: 620, height: 180 });
  } else {
    win = window.open("", "rt_subtitle_pip", "width=620,height=180,alwaysRaised=yes");
  }
  if (!win) return;

  pipWindow = win;
  win.document.title = "Live Subtitle";
  win.document.body.style.cssText =
    "margin:0;padding:12px 14px;background:#0c0d10;color:#fff;font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;display:flex;flex-direction:column;gap:8px;";
  win.document.body.innerHTML = `
    <div style="font-size:11px;color:#9ca3af;display:flex;justify-content:space-between;">
      <span>Live Subtitle</span><span>Always on top</span>
    </div>
    <div id="pipLines" style="display:flex;flex-direction:column;gap:8px;"></div>
  `;
}

function syncTtsLabels() {
  const outOn = el("outgoingTtsChk").checked;
  const inOn = el("incomingTtsChk").checked;
  el("outgoingTtsLabel").textContent = outOn ? "Suara AI" : "Hanya Teks";
  el("incomingTtsLabel").textContent = inOn ? "Suara AI" : "Hanya Teks";
  el("outgoingTtsChk").closest(".inline-toggle")?.classList.toggle("is-muted", !outOn);
  el("incomingTtsChk").closest(".inline-toggle")?.classList.toggle("is-muted", !inOn);
}

function applySavedControls(prefs) {
  if (!prefs) return;
  savedPrefs = { ...savedPrefs, ...prefs };
  if (prefs.mode) {
    el("modeSelect").value = prefs.mode;
    el("pttBtn").hidden = prefs.mode !== "ptt";
  }
  el("bypassChk").checked = Boolean(prefs.bypass);
  el("outgoingTtsChk").checked = prefs.outgoing_tts_enabled !== false;
  el("incomingTtsChk").checked = prefs.incoming_tts_enabled !== false;
  syncTtsLabels();

  const duckPct = Math.round((Number(prefs.ducking_level ?? 0.18)) * 100);
  el("duckSlider").value = String(duckPct);
  el("duckVal").textContent = `${duckPct}%`;

  const speedPct = Math.round((Number(prefs.tts_speed ?? 1.0)) * 100);
  el("ttsSpeedSlider").value = String(speedPct);
  el("ttsSpeedVal").textContent = `${(speedPct / 100).toFixed(2)}x`;

  const vadTh = Number(prefs.vad_energy_threshold ?? 350);
  el("vadThresholdSlider").value = String(vadTh);
  el("vadThresholdVal").textContent = String(vadTh);

  const vadSil = Number(prefs.vad_silence_ms ?? 750);
  el("vadSilenceSlider").value = String(vadSil);
  el("vadSilenceVal").textContent = `${vadSil} ms`;

  el("peerAutoLangChk").checked = Boolean(prefs.peer_auto_detect);
  if (prefs.translator_backend) {
    el("translatorBackendSelect").value = prefs.translator_backend;
    activeTranslatorBackend = prefs.translator_backend;
  }
}

let prefsSaveTimer = null;
function persistPrefsNow() {
  const cfg = getConfig();
  savedPrefs = { ...savedPrefs, ...cfg };
  fetch("/prefs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  })
    .then((r) => r.json())
    .then((data) => {
      if (data && data.translator_backend) {
        activeTranslatorBackend = data.translator_backend;
        const dev = engineLabel.match(/\(([^)]+)\)/)?.[1] || "cuda";
        engineLabel = `Ready · ${activeWhisperModel} (${dev}) · ${activeTranslatorBackend}`;
        if (ws && ws.readyState === WebSocket.OPEN) {
          el("engineStatus").textContent = engineLabel;
        }
      }
    })
    .catch(() => { });
}

function schedulePersistPrefs() {
  if (prefsSaveTimer) clearTimeout(prefsSaveTimer);
  prefsSaveTimer = setTimeout(persistPrefsNow, 150);
}

async function init() {
  const [langRes, devRes, healthRes, whisperRes, voicesRes, prefsRes] = await Promise.all([
    fetch("/languages").then((r) => r.json()),
    fetch("/audio/devices").then((r) => r.json()),
    fetch("/health").then((r) => r.json()).catch(() => null),
    fetch("/models/whisper").then((r) => r.json()).catch(() => null),
    fetch("/voices").then((r) => r.json()).catch(() => null),
    fetch("/prefs").then((r) => r.json()).catch(() => null),
  ]);
  applySavedControls(prefsRes);
  if (healthRes && healthRes.whisper_model) {
    activeWhisperModel = healthRes.whisper_model;
    activeTranslatorBackend = healthRes.translator_backend || activeTranslatorBackend;
    const dev = healthRes.whisper_device || "cpu";
    engineLabel = `Ready · ${activeWhisperModel} (${dev}) · ${activeTranslatorBackend}`;
    if (ws && ws.readyState === WebSocket.OPEN) {
      el("engineStatus").textContent = engineLabel;
    }
  }
  if (voicesRes && voicesRes.voices) {
    voicesByLang = voicesRes.voices;
  }
  populateLanguages(langRes.languages || []);
  refreshVoiceSelects(true);
  populateWhisperModels(whisperRes);
  applyDevices(devRes);
  connectControlWs();
}

el("whisperModelSelect").addEventListener("change", syncWhisperApplyBtn);

el("applyWhisperBtn").addEventListener("click", async () => {
  const sel = el("whisperModelSelect");
  const btn = el("applyWhisperBtn");
  const startBtn = el("startBtn");
  const targetModel = sel.value;
  if (!targetModel || targetModel === activeWhisperModel) return;

  sel.disabled = true;
  btn.disabled = true;
  btn.textContent = "Memuat...";
  startBtn.disabled = true;
  el("cancelWhisperBtn").disabled = false;
  el("whisperProgressWrap").hidden = false;
  el("whisperProgressBar").style.width = "0%";
  el("whisperProgressText").textContent = `0% · Menyiapkan model ${targetModel}...`;
  el("hintMsg").textContent = `Memuat model Whisper (${targetModel})...`;
  try {
    const resp = await fetch("/models/whisper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: targetModel }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || "Gagal mengganti model Whisper");
    }
    if (data.cancelled) {
      el("whisperModelSelect").value = activeWhisperModel;
      el("hintMsg").textContent = "Unduhan model Whisper dibatalkan.";
      return;
    }
    const whisperRes = await fetch("/models/whisper").then((r) => r.json());
    populateWhisperModels(whisperRes);
    const dev = data.active_device || "cpu";
    engineLabel = `Ready · ${data.active_model} (${dev}) · ${activeTranslatorBackend}`;
    el("engineStatus").textContent = engineLabel;
    el("hintMsg").textContent = `Model Whisper aktif: ${data.active_model} (${dev}).`;
  } catch (err) {
    el("hintMsg").textContent = `Gagal ganti model: ${err.message}`;
    el("whisperModelSelect").value = activeWhisperModel;
  } finally {
    el("whisperProgressWrap").hidden = true;
    sel.disabled = isRunning;
    startBtn.disabled = false;
    syncWhisperApplyBtn();
  }
});

el("cancelWhisperBtn").addEventListener("click", async () => {
  el("cancelWhisperBtn").disabled = true;
  el("whisperProgressText").textContent = "Membatalkan unduhan...";
  await fetch("/models/whisper/cancel", { method: "POST" }).catch(() => { });
});

el("refreshDevBtn").addEventListener("click", async () => {
  const devRes = await fetch("/audio/devices").then((r) => r.json());
  applyDevices(devRes);
});

el("setupVirtualBtn").addEventListener("click", async () => {
  el("hintMsg").textContent = "Menyiapkan Virtual Audio...";
  const res = await fetch("/audio/setup-virtual", { method: "POST" }).then((r) => r.json());
  if (res.ok) {
    applyDevices(res.devices);
    el("hintMsg").textContent = "Virtual Audio siap";
  } else {
    el("hintMsg").textContent = res.error || "Gagal setup Virtual Audio";
  }
});

el("swapLangBtn").addEventListener("click", () => {
  const a = el("myLang").value;
  el("myLang").value = el("peerLang").value;
  el("peerLang").value = a;
  refreshVoiceSelects(false);
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});

el("prepareBtn").addEventListener("click", async () => {
  const btn = el("prepareBtn");
  btn.disabled = true;
  el("cancelLangBtn").disabled = false;
  el("langProgressWrap").hidden = false;
  el("langProgressBar").style.width = "0%";
  el("langProgressText").textContent = "0% · Menyiapkan paket bahasa...";
  el("hintMsg").textContent = "Mengunduh paket bahasa & suara AI...";
  try {
    const resp = await fetch("/models/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        src_lang: el("myLang").value,
        dst_lang: el("peerLang").value,
        bidirectional: true,
        outgoing_voice: el("outgoingVoiceSelect").value,
        incoming_voice: el("incomingVoiceSelect").value,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || "Gagal mengunduh paket bahasa");
    }
    const voicesRes = await fetch("/voices").then((r) => r.json()).catch(() => null);
    if (voicesRes && voicesRes.voices) {
      voicesByLang = voicesRes.voices;
      refreshVoiceSelects(false);
    }
    el("hintMsg").textContent = data.cancelled
      ? "Unduhan paket bahasa dibatalkan."
      : "Paket bahasa & suara AI siap.";
  } catch (err) {
    el("hintMsg").textContent = `Gagal unduh: ${err.message}`;
  } finally {
    el("langProgressWrap").hidden = true;
    btn.disabled = isRunning;
  }
});

el("cancelLangBtn").addEventListener("click", async () => {
  el("cancelLangBtn").disabled = true;
  el("langProgressText").textContent = "Membatalkan unduhan...";
  await fetch("/models/prepare/cancel", { method: "POST" }).catch(() => { });
});

el("startBtn").addEventListener("click", () => {
  const btn = el("startBtn");
  if (isRunning) {
    btn.disabled = true;
    sendWs({ cmd: "stop" });
  } else {
    const cfg = getConfig();
    if (cfg.mic_source && cfg.vspeaker_monitor && cfg.mic_source === cfg.vspeaker_monitor) {
      el("hintMsg").textContent = "Mikrofon Fisik dan Virtual Speaker Monitor tidak boleh sama.";
      return;
    }
    btn.disabled = true;
    btn.textContent = "Menyiapkan...";
    setControlsLocked(true);
    el("hintMsg").textContent = "";
    sendWs({ cmd: "start", config: cfg });
  }
});

el("modeSelect").addEventListener("change", () => {
  const isPtt = el("modeSelect").value === "ptt";
  el("pttBtn").hidden = !isPtt;
  persistPrefsNow();
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});

let pttActive = false;
function setPttState(pressed) {
  if (pttActive === pressed) return;
  pttActive = pressed;
  sendWs({ cmd: "ptt", pressed });
}
el("pttBtn").addEventListener("mousedown", () => setPttState(true));
el("pttBtn").addEventListener("mouseup", () => setPttState(false));
el("pttBtn").addEventListener("mouseleave", () => setPttState(false));
el("pttBtn").addEventListener("touchstart", (e) => {
  e.preventDefault();
  setPttState(true);
});
el("pttBtn").addEventListener("touchend", () => setPttState(false));

["micSelect", "vmicSelect", "vspkSelect", "hpSelect"].forEach((id) => {
  el(id).addEventListener("change", persistPrefsNow);
});

["bypassChk", "outgoingTtsChk", "incomingTtsChk", "peerAutoLangChk", "translatorBackendSelect"].forEach((id) => {
  el(id).addEventListener("change", () => {
    syncTtsLabels();
    persistPrefsNow();
    if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
  });
});

el("duckSlider").addEventListener("input", () => {
  el("duckVal").textContent = `${el("duckSlider").value}%`;
  schedulePersistPrefs();
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});
el("ttsSpeedSlider").addEventListener("input", () => {
  el("ttsSpeedVal").textContent = `${(Number(el("ttsSpeedSlider").value) / 100).toFixed(2)}x`;
  schedulePersistPrefs();
});
el("vadThresholdSlider").addEventListener("input", () => {
  el("vadThresholdVal").textContent = el("vadThresholdSlider").value;
  schedulePersistPrefs();
});
el("vadSilenceSlider").addEventListener("input", () => {
  el("vadSilenceVal").textContent = `${el("vadSilenceSlider").value} ms`;
  schedulePersistPrefs();
});

["myLang", "peerLang"].forEach((id) => {
  el(id).addEventListener("change", () => {
    refreshVoiceSelects(false);
    persistPrefsNow();
    if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
  });
});

["outgoingVoiceSelect", "incomingVoiceSelect"].forEach((id) => {
  el(id).addEventListener("change", () => {
    persistPrefsNow();
    if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
  });
});

el("swapLangBtn").addEventListener("click", () => {
  const a = el("myLang").value;
  el("myLang").value = el("peerLang").value;
  el("peerLang").value = a;
  refreshVoiceSelects(false);
  persistPrefsNow();
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});

el("pipBtn").addEventListener("click", openPipOverlay);
el("testOutBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) sendWs({ cmd: "test_text", channel: "outgoing", text, config: getConfig() });
});
el("testInBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) sendWs({ cmd: "test_text", channel: "incoming", text, config: getConfig() });
});

init();

