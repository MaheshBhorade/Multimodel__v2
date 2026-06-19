#!/bin/bash
# ==============================================================================
# CRP Edge Agent - Raspberry Pi Audio Fix Script (Permanent)
# Resolves silent audio captures permanently by:
# 1. Enabling user linger for user 'indi' so systemd user services run at boot
# 2. Creating WirePlumber configurations to disable suspend-on-idle
# 3. Disabling PulseAudio suspend-on-idle module if present
# 4. Unmuting hardware interfaces and setting volumes to 100%
# 5. Restarting the systemd agent service
# ==============================================================================
set -e

echo "======================================================================"
echo "          Raspberry Pi HDMI Capture Audio Fix Utility (Permanent)     "
echo "======================================================================"

# 1. Check if running as root or current user has sudo capabilities
if [ "$EUID" -ne 0 ]; then
    echo "[Info] Requesting root privileges to configure ALSA mixer and restart services..."
    exec sudo "$0" "$@"
fi

# 2. Enable systemd lingering for user 'indi'
echo "[1/5] Enabling systemd user linger for 'indi'..."
loginctl enable-linger indi || echo "[Warn] Could not enable linger for user 'indi'"

# 2.5. Configure NOPASSWD sudo rules for USB reset
echo "[Step] Configuring passwordless sudo for USB unbind/bind reset..."
echo "indi ALL=(root) NOPASSWD: /usr/bin/tee /sys/bus/usb/drivers/usb/unbind, /usr/bin/tee /sys/bus/usb/drivers/usb/bind" > /etc/sudoers.d/indi-usb
chmod 0440 /etc/sudoers.d/indi-usb

# 3. Create permanent WirePlumber configurations for user 'indi' to disable suspend-on-idle
echo "[2/5] Creating WirePlumber configurations to disable suspend-on-idle..."
INDI_HOME="/home/indi"

# Create directories
mkdir -p "$INDI_HOME/.config/wireplumber/wireplumber.conf.d"
mkdir -p "$INDI_HOME/.config/wireplumber/main.lua.d"

# WirePlumber >= 0.5 (JSON format)
cat << 'EOF' > "$INDI_HOME/.config/wireplumber/wireplumber.conf.d/51-disable-suspension.conf"
monitor.alsa.rules = [
  {
    matches = [
      {
        device.name = "~alsa_card.*"
      }
    ]
    actions = {
      update-props = {
        session.suspend-timeout-seconds = 0
      }
    }
  }
]
EOF

# WirePlumber < 0.5 (Lua format)
cat << 'EOF' > "$INDI_HOME/.config/wireplumber/main.lua.d/51-disable-suspension.lua"
table.insert (alsa_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "alsa_input.*" },
    },
  },
  apply_properties = {
    ["session.suspend-timeout-seconds"] = 0,
  },
})
EOF

# Fix ownership of config directory
chown -R indi:indi "$INDI_HOME/.config/wireplumber"

# 4. Disable PulseAudio suspend-on-idle module if legacy PulseAudio config exists
if [ -f /etc/pulse/default.pa ]; then
    echo "[3/5] Disabling PulseAudio suspend-on-idle module..."
    sed -i 's/^load-module module-suspend-on-idle/# load-module module-suspend-on-idle/g' /etc/pulse/default.pa
    sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 pulseaudio -k 2>/dev/null || true
fi

# Restart wireplumber / pipewire user services
echo "      Restarting WirePlumber user service..."
sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 systemctl --user daemon-reload || true
sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart wireplumber.service 2>/dev/null || echo "[Info] WirePlumber user service restart not applicable or failed."

# 5. Configure ALSA mixer levels for Capture Card (typically Card 2 or default)
echo "[4/5] Configuring ALSA hardware mixer levels..."

# Unmute PCM and Digital In on card 2 (Macrosilicon capture card)
amixer -c 2 sset 'Digital In' on 2>/dev/null || echo "[Warn] Could not find 'Digital In' control on Card 2."
amixer -c 2 sset 'PCM' on 2>/dev/null || echo "[Warn] Could not find 'PCM' control on Card 2."

# Unmute any default capture systems
amixer sset 'Capture' 100% unmute 2>/dev/null || echo "[Info] Default 'Capture' control not present."
amixer sset 'Mic' 100% unmute 2>/dev/null || echo "[Info] Default 'Mic' control not present."

# Configure PipeWire/PulseAudio input sources
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
    echo "          Sending short audio ping to wake up suspended PipeWire hardware..."
    sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 arecord -D pulse -d 1 -f S16_LE -r 16000 -c 1 /dev/null 2>/dev/null || true
else
    echo "[Warn] No compatible PulseAudio/PipeWire capture source could be resolved at this moment."
fi

# 6. Restart the crp-edge systemd service
echo "[5/5] Restarting crp-edge systemd service..."
systemctl daemon-reload
systemctl restart crp-edge.service

echo "======================================================================"
echo "          Audio Fix completed! Edge agent has been restarted.        "
echo "======================================================================"
