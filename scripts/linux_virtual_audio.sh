#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-up}"
STATE_FILE="${XDG_RUNTIME_DIR:-/tmp}/rt_virtual_audio_modules"

if ! command -v pactl >/dev/null 2>&1; then
  echo "Error: pactl is not installed (PipeWire/PulseAudio required)." >&2
  exit 1
fi

teardown() {
  if [[ -f "$STATE_FILE" ]]; then
    while read -r mod_id; do
      [[ -n "$mod_id" ]] && pactl unload-module "$mod_id" 2>/dev/null || true
    done < "$STATE_FILE"
    rm -f "$STATE_FILE"
  fi
  # Clean any leftover modules matching our names
  pactl list short modules | awk '/RT_Virtual_Mic|RT_Virtual_Speaker/ {print $1}' | while read -r id; do
    pactl unload-module "$id" 2>/dev/null || true
  done
  echo "Virtual audio devices removed."
}

setup() {
  teardown >/dev/null 2>&1 || true

  # 1. Virtual Mic Sink + Remapped Source (select "RT_Virtual_Mic" as Mic in WhatsApp/Zoom/Meet)
  MIC_SINK_ID=$(pactl load-module module-null-sink \
    sink_name=RT_Virtual_Mic_Sink \
    sink_properties=device.description="RT_Virtual_Mic_Sink")

  MIC_SRC_ID=$(pactl load-module module-remap-source \
    master=RT_Virtual_Mic_Sink.monitor \
    source_name=RT_Virtual_Mic \
    source_properties=device.description="RT_Virtual_Mic")

  # 2. Virtual Speaker Sink (select "RT_Virtual_Speaker" as Speaker in WhatsApp/Zoom/Meet)
  SPK_SINK_ID=$(pactl load-module module-null-sink \
    sink_name=RT_Virtual_Speaker_Sink \
    sink_properties=device.description="RT_Virtual_Speaker")

  printf "%s\n%s\n%s\n" "$MIC_SRC_ID" "$MIC_SINK_ID" "$SPK_SINK_ID" > "$STATE_FILE"

  echo "Created virtual audio devices:"
  echo "  - Mic in WA/Zoom/Meet : RT_Virtual_Mic (Bridge writes to RT_Virtual_Mic_Sink)"
  echo "  - Spk in WA/Zoom/Meet : RT_Virtual_Speaker (Bridge captures from Monitor of RT_Virtual_Speaker)"
}

case "$ACTION" in
  up)
    setup
    ;;
  down)
    teardown
    ;;
  status)
    pactl list short sinks | grep -E "RT_Virtual_" || echo "No RT_Virtual sinks active."
    pactl list short sources | grep -E "RT_Virtual_" || echo "No RT_Virtual sources active."
    ;;
  *)
    echo "Usage: $0 [up|down|status]" >&2
    exit 1
    ;;
esac

