"""Build the two Windows executables (also works on Linux/macOS for testing).

    python packaging/build.py            -> dist/strikemirror-bridge/, dist/strikemirror-player/

The bridge (GPL-3.0, imports pymobiledevice3) and the player/client are built as SEPARATE
executables on purpose: they talk only over stdout JSON + HTTP on 127.0.0.1, which keeps the
process boundary the licensing plan relies on. Never merge them into one binary.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST, WORK = ROOT / "dist", ROOT / "build"


def pyinstaller(*args: str) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
           "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK), *args]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def build_bridge() -> None:
    pyinstaller(
        "--name", "strikemirror-bridge", "--console",
        # data files (viewer, notifications list, ...) and modules pulled in dynamically
        "--collect-data", "pymobiledevice3",
        "--collect-submodules", "pymobiledevice3.remote",
        "--collect-submodules", "pmd_pytcp",
        "--recursive-copy-metadata", "pymobiledevice3",
        str(ROOT / "bridge" / "ios_bridge.py"),
    )


def build_player() -> None:
    # The player runs the bridge as a child process; frozen, it finds strikemirror-bridge(.exe)
    # next to its own folder (see client/ios_client.py default_bridge_cmd).
    pyinstaller(
        "--name", "strikemirror-player", "--windowed" if sys.platform == "win32" else "--console",
        "--paths", str(ROOT / "client"),
        "--hidden-import", "recorder", "--hidden-import", "frame_fix",
        "--hidden-import", "sounddevice",        # imported lazily by AudioSession; hook adds PortAudio
        "--exclude-module", "pymobiledevice3",   # license boundary: the client must not contain it
        str(ROOT / "client" / "player.py"),
    )


if __name__ == "__main__":
    which = sys.argv[1:] or ["bridge", "player"]
    if "bridge" in which:
        build_bridge()
    if "player" in which:
        build_player()
