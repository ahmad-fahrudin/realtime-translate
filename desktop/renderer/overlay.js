const linesEl = document.getElementById("overlayLines");
const MAX_LINES = 3;

window.rtApi.onBridgeEvent((msg) => {
  if (msg.event !== "translation") return;

  const item = document.createElement("div");
  item.className = "overlay-line";
  const isPeer = msg.channel === "incoming";
  const label = isPeer ? `Lawan Bicara (${msg.dst_lang.toUpperCase()})` : `Anda (${msg.dst_lang.toUpperCase()})`;

  item.innerHTML = `
    <span class="overlay-tag ${isPeer ? "peer" : ""}"></span>
    <span class="overlay-text"></span>
  `;
  item.querySelector(".overlay-tag").textContent = label;
  item.querySelector(".overlay-text").textContent = msg.translated_text;

  linesEl.appendChild(item);
  while (linesEl.children.length > MAX_LINES) {
    linesEl.removeChild(linesEl.firstChild);
  }
});

