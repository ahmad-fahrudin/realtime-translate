const el = (id) => document.getElementById(id);

let ws = null;
let isRunning = false;
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
  vad_aggressiveness: 2,
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
    vad_aggressiveness: Number(el("vadAggSelect").value),
    peer_auto_detect: el("peerAutoLangChk").checked,
    translator_backend: el("translatorBackendSelect").value,
  };
}

function isFeedNearBottom() {
  const feed = el("feed");
  return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
}

function updateThreshMarker() {
  const mark = el("vadThreshMark");
  if (!mark) return;
  const val = Number(el("vadThresholdSlider").value);
  const pct = Math.max(0, Math.min(100, (val / 950) * 100));
  mark.style.left = `${pct}%`;
}

function setMicMeter(rms) {
  const fill = el("micRmsFill");
  const valEl = el("micRmsVal");
  if (!fill || !valEl) return;
  const clean = Math.max(0, Number(rms) || 0);
  const pct = Math.max(0, Math.min(100, (clean / 950) * 100));
  const thresh = Number(el("vadThresholdSlider").value) || 350;
  fill.style.width = `${pct}%`;
  fill.classList.toggle("is-hot", clean >= thresh);
  valEl.textContent = `RMS ${Math.round(clean)}`;
}

function setPendingBubble(channel, active) {
  const feed = el("feed");
  const existing = feed.querySelector(`.msg-pending[data-channel="${channel}"]`);
  if (!active) {
    if (existing) existing.remove();
    return;
  }
  if (existing) return;
  const nearBottom = isFeedNearBottom();
  const out = channel === "outgoing";
  const div = document.createElement("div");
  div.className = `msg msg-pending ${out ? "msg-out" : "msg-in"}`;
  div.dataset.channel = channel;
  div.innerHTML = `
    <div class="bubble">
      <span class="typing-dots"><span></span><span></span><span></span></span>
      <span>Menerjemahkan...</span>
    </div>
  `;
  feed.appendChild(div);
  if (nearBottom || out) feed.scrollTop = feed.scrollHeight;
}

function appendFeed(item, opts = {}) {
  const shouldScroll = opts.scroll !== false;
  const feed = el("feed");
  const out = item.channel === "outgoing";
  const nearBottom = isFeedNearBottom();
  setPendingBubble(item.channel, false);

  const div = document.createElement("article");
  div.className = `msg ${out ? "msg-out" : "msg-in"}`;
  const tag = out ? "Anda" : "Lawan Bicara";
  const warn = item.tts_warning ? ` · ${item.tts_warning}` : "";
  const hasStages = typeof item.mt_ms === "number";
  const timing = hasStages
    ? `${item.latency_ms} ms · STT ${item.stt_ms || 0} · MT ${item.mt_ms} · TTS ${item.tts_ms || 0}`
    : `${item.latency_ms} ms`;
  const dt = item.ts_ms ? new Date(item.ts_ms) : new Date();
  const time = dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  div.innerHTML = `
    <div class="msg-meta">
      <span class="msg-who"></span>
      <span class="msg-time"></span>
      <button type="button" class="copy-btn">Salin</button>
    </div>
    <div class="bubble">
      <div class="feed-dst"></div>
      <div class="feed-src"></div>
    </div>
    <div class="msg-foot"></div>
  `;
  div.querySelector(".msg-who").textContent = `${tag} · ${item.src_lang} → ${item.dst_lang}`;
  div.querySelector(".msg-time").textContent = time;
  div.querySelector(".feed-src").textContent = item.source_text;
  div.querySelector(".feed-dst").textContent = item.translated_text;
  div.querySelector(".msg-foot").textContent = `${timing}${warn}`;

  const copyBtn = div.querySelector(".copy-btn");
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(item.translated_text || "");
      copyBtn.textContent = "Tersalin";
      copyBtn.classList.add("is-copied");
      setTimeout(() => {
        copyBtn.textContent = "Salin";
        copyBtn.classList.remove("is-copied");
      }, 1500);
    } catch {
      /* ignore clipboard error */
    }
  });

  feed.appendChild(div);
  if (shouldScroll) {
    if (nearBottom || out) {
      feed.scrollTop = feed.scrollHeight;
      el("jumpLatestBtn").hidden = true;
    } else {
      el("jumpLatestBtn").hidden = false;
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
  "bypassChk",
  "translatorBackendSelect",
  "measureNoiseBtn",
  "testInput",
  "testOutBtn",
  "testInBtn",
];

function setControlsLocked(locked) {
  for (const id of LOCKABLE_CONTROL_IDS) {
    const node = el(id);
    if (node) node.disabled = locked;
  }
  const bypassWrap = el("bypassChk")?.closest(".toggle-chip");
  if (bypassWrap) bypassWrap.classList.toggle("is-disabled", locked);
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
  if (!running) {
    setMicMeter(0);
    setPendingBubble("outgoing", false);
    setPendingBubble("incoming", false);
    setPttState(false);
  }
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
    } else if (msg.event === "vad") {
      const dot = msg.channel === "outgoing" ? el("micVadDot") : el("peerVadDot");
      dot.className = msg.active ? "dot active" : "dot";
    } else if (msg.event === "audio_level") {
      if (msg.channel === "outgoing") {
        setMicMeter(msg.rms);
      }
    } else if (msg.event === "processing") {
      setPendingBubble(msg.channel, Boolean(msg.active));
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
  updateThreshMarker();

  const vadSil = Number(prefs.vad_silence_ms ?? 750);
  el("vadSilenceSlider").value = String(vadSil);
  el("vadSilenceVal").textContent = `${vadSil} ms`;

  if (prefs.vad_aggressiveness !== undefined) {
    el("vadAggSelect").value = String(prefs.vad_aggressiveness);
  }

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

async function loadHistory(query = "") {
  const url = query
    ? `/history?limit=200&q=${encodeURIComponent(query)}`
    : "/history?limit=200";
  const res = await fetch(url).then((r) => r.json()).catch(() => null);
  const feed = el("feed");
  feed.innerHTML = "";
  el("jumpLatestBtn").hidden = true;
  if (res && Array.isArray(res.items)) {
    for (const item of res.items) {
      appendFeed(item, { scroll: false });
    }
    feed.scrollTop = feed.scrollHeight;
  }
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
  }
  if (voicesRes && voicesRes.voices) {
    voicesByLang = voicesRes.voices;
  }
  populateLanguages(langRes.languages || []);
  refreshVoiceSelects(true);
  populateWhisperModels(whisperRes);
  applyDevices(devRes);
  await loadHistory();
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
  const btn = el("pttBtn");
  if (btn) {
    btn.classList.toggle("is-pressing", pressed);
    btn.textContent = pressed ? "Sedang Merekam..." : "Tahan untuk Bicara (Spasi)";
  }
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

function isEditableTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

window.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat) return;
  if (!isRunning || el("modeSelect").value !== "ptt" || isEditableTarget(e.target)) return;
  e.preventDefault();
  setPttState(true);
});

window.addEventListener("keyup", (e) => {
  if (e.code !== "Space" || !pttActive) return;
  e.preventDefault();
  setPttState(false);
});

window.addEventListener("blur", () => {
  if (pttActive) setPttState(false);
});

["micSelect", "vmicSelect", "vspkSelect", "hpSelect"].forEach((id) => {
  el(id).addEventListener("change", persistPrefsNow);
});

["bypassChk", "outgoingTtsChk", "incomingTtsChk", "peerAutoLangChk", "translatorBackendSelect", "vadAggSelect"].forEach((id) => {
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
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});
el("vadThresholdSlider").addEventListener("input", () => {
  el("vadThresholdVal").textContent = el("vadThresholdSlider").value;
  updateThreshMarker();
  schedulePersistPrefs();
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});
el("vadSilenceSlider").addEventListener("input", () => {
  el("vadSilenceVal").textContent = `${el("vadSilenceSlider").value} ms`;
  schedulePersistPrefs();
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});

el("measureNoiseBtn").addEventListener("click", async () => {
  const btn = el("measureNoiseBtn");
  btn.disabled = true;
  btn.textContent = "Mengukur...";
  try {
    const resp = await fetch("/audio/measure-noise", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mic_source: el("micSelect").value }),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      throw new Error(data.detail || data.error || "Gagal mengukur kebisingan");
    }
    setMicMeter(data.rms_avg);
    el("vadThresholdSlider").value = String(data.suggested_threshold);
    el("vadThresholdVal").textContent = String(data.suggested_threshold);
    updateThreshMarker();
    persistPrefsNow();
    el("hintMsg").textContent = `Kebisingan RMS ${data.rms_avg} · Threshold disetel ke ${data.suggested_threshold}`;
  } catch (err) {
    el("hintMsg").textContent = err.message;
  } finally {
    btn.disabled = isRunning;
    btn.textContent = "Ukur";
  }
});

let searchTimer = null;
el("historySearch").addEventListener("input", () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadHistory(el("historySearch").value.trim()), 180);
});

el("exportBtn").addEventListener("click", () => {
  const q = el("historySearch").value.trim();
  const url = q
    ? `/history/export?q=${encodeURIComponent(q)}`
    : "/history/export";
  const a = document.createElement("a");
  a.href = url;
  a.download = "transkrip.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
});

el("clearHistoryBtn").addEventListener("click", async () => {
  if (!confirm("Bersihkan seluruh riwayat transkrip?")) return;
  await fetch("/history", { method: "DELETE" }).catch(() => { });
  el("feed").innerHTML = "";
  el("jumpLatestBtn").hidden = true;
});

el("feed").addEventListener("scroll", () => {
  if (isFeedNearBottom()) {
    el("jumpLatestBtn").hidden = true;
  }
});

el("jumpLatestBtn").addEventListener("click", () => {
  const feed = el("feed");
  feed.scrollTop = feed.scrollHeight;
  el("jumpLatestBtn").hidden = true;
});

el("testInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    el("testOutBtn").click();
  }
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

el("testOutBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) sendWs({ cmd: "test_text", channel: "outgoing", text, config: getConfig() });
});
el("testInBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) sendWs({ cmd: "test_text", channel: "incoming", text, config: getConfig() });
});

function initSettingsTabs() {
  const tabs = document.querySelectorAll(".nav-tab[data-tab]");
  const panes = document.querySelectorAll(".settings-pane[data-pane]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.tab;
      tabs.forEach((t) => {
        const active = t === tab;
        t.classList.toggle("is-active", active);
        t.setAttribute("aria-selected", active ? "true" : "false");
      });
      panes.forEach((pane) => {
        pane.hidden = pane.dataset.pane !== target;
      });
    });
  });
}

initSettingsTabs();
init();



(function initTheme() {
  const root = document.documentElement;
  const btns = document.querySelectorAll("[data-theme-opt]");
  const apply = (mode) => {
    if (mode === "light" || mode === "dark") {
      root.dataset.theme = mode;
      localStorage.setItem("theme", mode);
    } else {
      delete root.dataset.theme;
      localStorage.removeItem("theme");
    }
    btns.forEach((b) => {
      const on = b.dataset.themeOpt === (mode === "light" || mode === "dark" ? mode : "system");
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
  };
  btns.forEach((b) => b.addEventListener("click", () => apply(b.dataset.themeOpt)));
  apply(localStorage.getItem("theme"));
})();
