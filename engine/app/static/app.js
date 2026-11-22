const el = (id) => document.getElementById(id);

let ws = null;
let isRunning = false;
let pipWindow = null;

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

function populateDeviceSelect(selectEl, devices, preferredMatchers = [], excludeMatchers = []) {
  const prev = selectEl.value;
  selectEl.innerHTML = "";
  selectEl.add(new Option("-- Tidak dipilih --", ""));
  let autoPick = "";

  for (const d of devices) {
    selectEl.add(new Option(d.name, d.id));
    const excluded = excludeMatchers.some((rx) => rx.test(d.id) || rx.test(d.name));
    if (!excluded && !autoPick && preferredMatchers.some((rx) => rx.test(d.id) || rx.test(d.name))) {
      autoPick = d.id;
    }
  }
  if (prev && [...selectEl.options].some((o) => o.value === prev)) {
    selectEl.value = prev;
  } else if (autoPick) {
    selectEl.value = autoPick;
  }
}

function applyDevices(devices) {
  populateDeviceSelect(
    el("micSelect"),
    devices.inputs || [],
    [/alsa_input\./i, /mic|headset/i],
    [/\.monitor$/i, /RT_Virtual_/i]
  );
  populateDeviceSelect(el("vmicSelect"), devices.outputs || [], [/^RT_Virtual_Mic_Sink$/i]);
  populateDeviceSelect(el("vspkSelect"), devices.inputs || [], [/^RT_Virtual_Speaker_Sink\.monitor$/i]);
  populateDeviceSelect(
    el("hpSelect"),
    devices.outputs || [],
    [/alsa_output\./i, /analog|headphone|speaker/i],
    [/RT_Virtual_/i]
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

function setRunningState(running) {
  isRunning = running;
  const btn = el("startBtn");
  btn.textContent = running ? "Hentikan Translasi" : "Mulai Translasi";
  btn.className = running ? "danger" : "primary";
}

function connectControlWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/control`);

  ws.onopen = () => {
    el("engineDot").className = "dot ok";
    el("engineStatus").textContent = "Engine: Ready";
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
  const [langRes, devRes] = await Promise.all([
    fetch("/languages").then((r) => r.json()),
    fetch("/audio/devices").then((r) => r.json()),
  ]);
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
  if (isRunning) {
    sendWs({ cmd: "stop" });
  } else {
    el("hintMsg").textContent = "";
    sendWs({ cmd: "start", config: getConfig() });
  }
});

el("modeSelect").addEventListener("change", () => {
  const isPtt = el("modeSelect").value === "ptt";
  el("pttBtn").hidden = !isPtt;
  if (isRunning) sendWs({ cmd: "update_config", config: getConfig() });
});

el("pttBtn").addEventListener("mousedown", () => sendWs({ cmd: "ptt", pressed: true }));
el("pttBtn").addEventListener("mouseup", () => sendWs({ cmd: "ptt", pressed: false }));
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

