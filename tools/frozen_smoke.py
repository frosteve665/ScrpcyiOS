"""Smoke-test the PyInstaller builds WITHOUT an iPhone (used by CI on windows-latest).

    python tools/frozen_smoke.py dist

Passes when:
  1. the frozen bridge starts, imports pymobiledevice3, and reports a clean JSON error event
     (no device / no Apple driver on a CI runner) with exit code 2;
  2. the frozen player starts, finds the sibling bridge, gets that error, and exits with code 3;
  3. the player bundle does not contain pymobiledevice3 (license boundary);
  4. on Windows the player bundle carries the PortAudio DLL that sounddevice loads for audio.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EXPECTED = {"driver_missing", "device_not_found"}


def exe(dist: Path, name: str) -> Path:
    return dist / name / (name + (".exe" if sys.platform == "win32" else ""))


def check_bridge(dist: Path) -> list[str]:
    p = subprocess.run([str(exe(dist, "strikemirror-bridge")), "--no-log"], capture_output=True, text=True,
                       timeout=120)
    events = []
    for line in p.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    last = events[-1] if events else {}
    errs = []
    if p.returncode != 2:
        errs.append(f"bridge exit code {p.returncode}, expected 2; stderr tail: {p.stderr[-800:]}")
    if last.get("event") != "error" or last.get("code") not in EXPECTED:
        errs.append(f"bridge last event {last!r}, expected error with code in {sorted(EXPECTED)}")
    print("bridge:", p.returncode, last)
    return errs


def check_player(dist: Path) -> list[str]:
    try:
        p = subprocess.run([str(exe(dist, "strikemirror-player"))], capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return ["player did not exit (an import error dialog may be open)"]
    print("player:", p.returncode)
    return [] if p.returncode == 3 else [f"player exit code {p.returncode}, expected 3 (bridge error)"]


def check_boundary(dist: Path) -> list[str]:
    hits = [str(x) for x in (dist / "strikemirror-player").rglob("pymobiledevice3*")]
    return [f"player bundle contains pymobiledevice3: {hits[:3]}"] if hits else []


def check_audio_runtime(dist: Path, platform: str = sys.platform) -> list[str]:
    if platform != "win32":   # Linux/macOS builds use the system PortAudio
        return []
    hits = list((dist / "strikemirror-player").rglob("libportaudio64bit*.dll"))
    return [] if hits else ["player bundle has no PortAudio DLL (sounddevice not collected): audio would be off"]


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    errs = check_bridge(dist) + check_boundary(dist) + check_audio_runtime(dist)
    if "--skip-player" not in sys.argv:
        errs += check_player(dist)
    for e in errs:
        print("FAIL:", e)
    print("OK" if not errs else f"{len(errs)} failure(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
