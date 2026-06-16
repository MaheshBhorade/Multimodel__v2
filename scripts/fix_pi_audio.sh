#!/bin/bash
# ==============================================================================
# CRP Edge Agent - Raspberry Pi Audio Fix Script
# Resolves silent audio captures by unmuting hardware interfaces, un-suspending 
# PipeWire sources, setting volume to 100%, and restarting the agent service.
# ==============================================================================
set -e

echo "======================================================================"
echo "          Raspberry Pi HDMI Capture Audio Fix Utility                "
echo "======================================================================"

# 1. Check if running as root or current user has sudo capabilities
if [ "$EUID" -ne 0 ]; then
    echo "[Info] Requesting root privileges to configure ALSA mixer and restart services..."
    exec sudo "$0" "$@"
fi

# 2. Configure ALSA mixer levels for Capture Card (typically Card 2 or default)
echo "[1/4] Configuring ALSA hardware mixer levels..."

# Unmute PCM and Digital In on card 2 (Macrosilicon capture card)
amixer -c 2 sset 'Digital In' on 2>/dev/null || echo "[Warn] Could not find 'Digital In' control on Card 2."
amixer -c 2 sset 'PCM' on 2>/dev/null || echo "[Warn] Could not find 'PCM' control on Card 2."

# Unmute any default capture systems
amixer sset 'Capture' 100% unmute 2>/dev/null || echo "[Info] Default 'Capture' control not present."
amixer sset 'Mic' 100% unmute 2>/dev/null || echo "[Info] Default 'Mic' control not present."

# 3. Find and configure PipeWire/PulseAudio input sources
echo "[2/4] Configuring PipeWire / PulseAudio sound server..."

# Identify the target Macrosilicon USB Audio Input source (ignoring output monitor ports)
TARGET_SOURCE=$(sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 pactl list sources short 2>/dev/null | grep -E "MACROSILICON|Macrosilicon|c3-1|usb" | grep -v -E "monitor|output" | awk '{print $2}' | head -n 1)

if [ -z "$TARGET_SOURCE" ]; then
    echo "[Info] Macrosilicon source not found. Checking general analog stereo input sources..."
    TARGET_SOURCE=$(sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 pactl list sources short 2>/dev/null | grep "input" | grep -v "monitor" | awk '{print $2}' | head -n 1)
fi

if [ -n "$TARGET_SOURCE" ]; then
    echo "[Success] Detected active HDMI Audio input source: $TARGET_SOURCE"
    echo "          Unmuting source and setting volume to 100%..."
    sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 pactl set-source-mute "$TARGET_SOURCE" 0 || echo "[Warn] Failed to unmute source."
    sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 pactl set-source-volume "$TARGET_SOURCE" 100% || echo "[Warn] Failed to set volume to 100%."
    
    # Trigger a 1-second recording to wake up the PipeWire source from a SUSPENDED state
    echo "[3/4] Sending short audio ping to wake up suspended PipeWire hardware..."
    sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 arecord -D pulse -d 1 -f S16_LE -r 16000 -c 1 /dev/null 2>/dev/null || true
else
    echo "[Error] No compatible PulseAudio/PipeWire capture source could be resolved!"
    echo "        Check if the HDMI Capture Card is plugged in securely (run 'lsusb')."
fi

# 4. Restart the crp-edge systemd service
echo "[4/4] Restarting crp-edge systemd service..."
systemctl daemon-reload
systemctl restart crp-edge.service

echo "======================================================================"
echo "          Audio Fix completed! Edge agent has been restarted.        "
echo "======================================================================"
