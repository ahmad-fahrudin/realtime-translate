const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("rtApi", {
  sendBridgeCmd: (cmdObj) => ipcRenderer.invoke("bridge:cmd", cmdObj),
  toggleOverlay: (show) => ipcRenderer.invoke("overlay:toggle", show),
  getEngineHealth: () => ipcRenderer.invoke("engine:health"),
  getLanguages: () => ipcRenderer.invoke("engine:languages"),
  prepareModels: (src_lang, dst_lang) => ipcRenderer.invoke("engine:prepare", { src_lang, dst_lang }),
  setupLinuxVirtualAudio: () => ipcRenderer.invoke("linux:setup-virtual-audio"),
  onBridgeEvent: (cb) => {
    const listener = (_ev, data) => cb(data);
    ipcRenderer.on("bridge-event", listener);
    return () => ipcRenderer.removeListener("bridge-event", listener);
  },
});

