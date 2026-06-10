from __future__ import annotations

import os
import subprocess
from pathlib import Path


def discover_audio_device() -> str:
    env_device = os.environ.get("CRP_AUDIO_DEVICE")
    if env_device:
        return env_device

    cards_path = Path("/proc/asound/cards")
    if not cards_path.exists():
        return "default"

    try:
        for line in cards_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if any(token in line for token in ("Video", "USB-Audio", "Macrosilicon", "C3-1 USB3")):
                parts = line.strip().split()
                if parts and parts[0].isdigit():
                    return f"plughw:{parts[0]},0"
    except OSError:
        return "default"

    return "default"


def discover_video_device() -> str | None:
    try:
        output = subprocess.check_output(
            ["v4l2-ctl", "--list-devices"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        output = ""

    preferred_names = ("USB3 Video", "Macrosilicon", "HDMI", "Capture")
    for block in output.split("\n\n"):
        if any(name in block for name in preferred_names):
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("/dev/video"):
                    return line

    for idx in range(3):
        device_path = f"/dev/video{idx}"
        if os.path.exists(device_path):
            return device_path

    return None


def candidate_video_indices() -> list[int]:
    preferred: list[int] = []
    device = discover_video_device()
    if device and device.startswith("/dev/video"):
        try:
            preferred.append(int(device.replace("/dev/video", "")))
        except ValueError:
            pass

    for idx in range(3):
        if idx not in preferred and os.path.exists(f"/dev/video{idx}"):
            preferred.append(idx)

    return preferred
