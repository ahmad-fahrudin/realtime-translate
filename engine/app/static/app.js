const el = (id) => document.getElementById(id);

let ws = null;
let isRunning = false;
let pipWindow = null;
let engineLabel = "Engine: Ready";

function populateLanguages(items) {
  const myLang = el("myLang");
  const peerLang = el("peerLang");
  const prevMy = myLang.value || "id";
  const prevPeer = peerLang.value || "en";

  myLang.innerHTML = "";
  peerLang.innerHTML = "";
  for (const lang of items) {
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

  populateDeviceSelect(el("micSelect"), physicalMics, [/bluez_input\./i, /alsa_input\./i, /mic|headset/i]);
  populateDeviceSelect(el("vmicSelect"), virtualMicSinks, [/^RT_Virtual_Mic_Sink$/i]);
  populateDeviceSelect(el("vspkSelect"), virtualSpeakerMonitors, [/^RT_Virtual_Speaker_Sink\.monitor$/i]);
  populateDeviceSelect(el("hpSelect"), physicalOutputs, [/bluez_output\./i, /alsa_output\./i, /analog|headphone|speaker/i]);
}

function getConfig() {
  return {
    mic_source: el("micSelect").value,
    vmic_sink: el("vmicSelect").value,
    vspeaker_monitor: el("vspkSelect").value,
    headphone_sink: el("hpSelect").value,
    my_lang: el("myLang").value,
    peer_lang: el("peerLang").value,
    mode: el("modeSelect").value,
    bypass: el("bypassChk").checked,
    ducking_level: Number(el("duckSlider").value) / 100,
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
  "prepareBtn",
  "setupVirtualBtn",
  "refreshDevBtn",
  "micSelect",
  "vmicSelect",
  "vspkSelect",
  "hpSelect",
  "modeSelect",
  "bypassChk",
  "duckSlider",
  "testInput",
  "testOutBtn",
  "testInBtn",
];

function setControlsLocked(locked) {
  for (const id of LOCKABLE_CONTROL_IDS) {
    const node = el(id);
    if (node) node.disabled = locked;
  }
  const bypassChip = el("bypassChk")?.closest(".toggle-chip");
  if (bypassChip) bypassChip.classList.toggle("is-disabled", locked);
}

function setRunningState(running) {
  isRunning = running;
  const btn = el("startBtn");
  btn.disabled = false;
  btn.textContent = running ? "Hentikan Translasi" : "Mulai Translasi";
  btn.className = running ? "danger" : "primary";
  setControlsLocked(running);
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

async function init() {
  const [langRes, devRes, healthRes] = await Promise.all([
    fetch("/languages").then((r) => r.json()),
    fetch("/audio/devices").then((r) => r.json()),
    fetch("/health").then((r) => r.json()).catch(() => null),
  ]);
  if (healthRes && healthRes.whisper_model) {
    const dev = healthRes.whisper_device || "cpu";
    const mt = healthRes.translator_backend || "nllb";
    engineLabel = `Ready · ${healthRes.whisper_model} (${dev}) · ${mt}`;
    if (ws && ws.readyState === WebSocket.OPEN) {
      el("engineStatus").textContent = engineLabel;
    }
  }
  populateLanguages(langRes.languages || []);
  applyDevices(devRes);
  connectControlWs();
}

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
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});

el("prepareBtn").addEventListener("click", async () => {
  el("hintMsg").textContent = "Mengunduh paket bahasa...";
  try {
    await fetch("/models/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ src_lang: el("myLang").value, dst_lang: el("peerLang").value }),
    });
    await fetch("/models/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ src_lang: el("peerLang").value, dst_lang: el("myLang").value }),
    });
    el("hintMsg").textContent = "Paket bahasa siap.";
  } catch (err) {
    el("hintMsg").textContent = `Gagal unduh: ${err.message}`;
  }
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
el("bypassChk").addEventListener("change", () => {
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});
el("duckSlider").addEventListener("input", () => {
  el("duckVal").textContent = `${el("duckSlider").value}%`;
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});
["myLang", "peerLang"].forEach((id) => {
  el(id).addEventListener("change", () => {
    if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
  });
});

el("pipBtn").addEventListener("click", openPipOverlay);
el("testOutBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) sendWs({ cmd: "test_text", channel: "outgoing", text });
});
el("testInBtn").addEventListener("click", () => {
  const text = el("testInput").value.trim();
  if (text) sendWs({ cmd: "test_text", channel: "incoming", text });
});

init();

