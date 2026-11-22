const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const path = require("path");

let controlWin = null;
let overlayWin = null;
let bridgeProc = null;

const ENGINE_HTTP = process.env.RT_ENGINE_HTTP || "http://localhost:8765";

function resolvePythonBin() {
  const candidates = [
    path.join(__dirname, "bridge", ".venv", "bin", "python"),
    path.join(__dirname, "bridge", ".venv", "Scripts", "python.exe"),
    process.platform === "win32" ? "python" : "python3",
  ];
  for (const bin of candidates) {
    if (bin.includes(path.sep) ? fs.existsSync(bin) : true) {
      return bin;
    }
  }
  return "python3";
}

function sendToAll(channel, payload) {
  if (controlWin && !controlWin.isDestroyed()) {
    controlWin.webContents.send(channel, payload);
  }
  if (overlayWin && !overlayWin.isDestroyed()) {
    overlayWin.webContents.send(channel, payload);
  }
}

function startBridge() {
  if (bridgeProc) return;
  const py = resolvePythonBin();
  const script = path.join(__dirname, "bridge", "audio_bridge.py");

  bridgeProc = spawn(py, ["-u", script], {
    stdio: ["pipe", "pipe", "pipe"],
  });

  let stdoutBuf = "";
  bridgeProc.stdout.on("data", (chunk) => {
    stdoutBuf += chunk.toString("utf8");
    let nlIdx;
    while ((nlIdx = stdoutBuf.indexOf("\n")) !== -1) {
      const line = stdoutBuf.slice(0, nlIdx).trim();
      stdoutBuf = stdoutBuf.slice(nlIdx + 1);
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        sendToAll("bridge-event", msg);
      } catch {
        // Ignore partial non-JSON lines
      }
    }
  });

  bridgeProc.stderr.on("data", (chunk) => {
    const errText = chunk.toString("utf8").trim();
    if (errText) {
      sendToAll("bridge-event", { event: "error", message: errText });
    }
  });

  bridgeProc.on("exit", (code) => {
    bridgeProc = null;
    sendToAll("bridge-event", { event: "status", running: false, exit_code: code });
  });
}

function sendBridgeCmd(obj) {
  if (!bridgeProc) startBridge();
  if (bridgeProc && bridgeProc.stdin.writable) {
    bridgeProc.stdin.write(JSON.stringify(obj) + "\n");
  }
}

function createControlWindow() {
  controlWin = new BrowserWindow({
    width: 820,
    height: 700,
    minWidth: 680,
    minHeight: 580,
    backgroundColor: "#0c0d10",
    title: "Realtime Translate",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  controlWin.setMenuBarVisibility(false);
  controlWin.loadFile(path.join(__dirname, "renderer", "control.html"));
  controlWin.on("closed", () => {
    controlWin = null;
    if (overlayWin && !overlayWin.isDestroyed()) overlayWin.close();
  });
}

function createOverlayWindow() {
  if (overlayWin && !overlayWin.isDestroyed()) {
    overlayWin.show();
    return;
  }
  overlayWin = new BrowserWindow({
    width: 680,
    height: 170,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  overlayWin.loadFile(path.join(__dirname, "renderer", "overlay.html"));
  overlayWin.on("closed", () => {
    overlayWin = null;
  });
}

app.whenReady().then(() => {
  startBridge();
  createControlWindow();
});

app.on("window-all-closed", () => {
  if (bridgeProc) {
    bridgeProc.kill();
    bridgeProc = null;
  }
  if (process.platform !== "darwin") app.quit();
});

ipcMain.handle("bridge:cmd", (_ev, cmdObj) => {
  sendBridgeCmd(cmdObj);
  return true;
});

ipcMain.handle("overlay:toggle", (_ev, show) => {
  if (show) {
    createOverlayWindow();
    return true;
  }
  if (overlayWin && !overlayWin.isDestroyed()) {
    overlayWin.close();
    overlayWin = null;
  }
  return false;
});

ipcMain.handle("engine:health", async () => {
  try {
    const res = await fetch(`${ENGINE_HTTP}/health`);
    return await res.json();
  } catch (err) {
    return { status: "offline", error: err.message };
  }
});

ipcMain.handle("engine:languages", async () => {
  try {
    const res = await fetch(`${ENGINE_HTTP}/languages`);
    return await res.json();
  } catch (err) {
    return { languages: [], error: err.message };
  }
});

ipcMain.handle("engine:prepare", async (_ev, { src_lang, dst_lang }) => {
  const res = await fetch(`${ENGINE_HTTP}/models/prepare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ src_lang, dst_lang }),
  });
  return await res.json();
});

ipcMain.handle("linux:setup-virtual-audio", async () => {
  if (process.platform !== "linux") {
    return { ok: false, message: "Only applicable on Linux PipeWire/PulseAudio." };
  }
  const scriptPath = path.resolve(__dirname, "..", "scripts", "linux_virtual_audio.sh");
  return new Promise((resolve) => {
    execFile("bash", [scriptPath, "up"], (err, stdout, stderr) => {
      if (err) {
        resolve({ ok: false, message: stderr || err.message });
      } else {
        sendBridgeCmd({ cmd: "list_devices" });
        resolve({ ok: true, message: stdout.trim() });
      }
    });
  });
});

