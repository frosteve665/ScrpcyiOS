"""Hardware smoke test: python tools/smoke_test.py [--udid UDID] [--frames 60]

Starts the bridge, waits for video, sends one tap in a safe corner, prints a JSON report.
Exit code 0 = pass. Used by the self-hosted CI runner that has an iPhone attached.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))
import ios_client as c  # noqa: E402


def run_smoke(api: "c.BridgeHttp", min_frames: int = 60, timeout: float = 30.0, tap: bool = True) -> dict:
    report = {"ok": False, "frames": 0, "fps": 0.0, "width": None, "height": None,
              "first_frame_ms": None, "touch_sent": False, "error": None}
    video = c.VideoSession(api)
    started = time.monotonic()
    video.start()
    try:
        last, first_at, deadline = 0, None, started + timeout
        while time.monotonic() < deadline and video.frames < min_frames:
            video.wait_frame(last, 0.2)
            last = video.frames
            if first_at is None and video.frames:
                first_at = time.monotonic()
                report["first_frame_ms"] = int((first_at - started) * 1000)
        report["frames"] = video.frames
        if video.latest is not None:
            report["width"], report["height"] = video.latest.width, video.latest.height
        if first_at and video.frames > 1:
            report["fps"] = round((video.frames - 1) / max(time.monotonic() - first_at, 1e-6), 1)
        if tap and video.frames:
            inp = c.InputController(api)
            inp.tap(0.95, 0.02)  # status-bar corner: harmless
            time.sleep(0.3)
            inp.close()
            report["touch_sent"] = True
        report["ok"] = video.frames >= min_frames and (report["touch_sent"] or not tap)
        if not report["ok"]:
            report["error"] = video.last_error or "not enough frames before timeout"
    finally:
        video.stop()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udid")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--timeout", type=float, default=45.0)
    args = ap.parse_args()
    bridge = c.BridgeProcess(args.udid, extra_args=["--no-audio"])
    try:
        info = bridge.start()
    except c.BridgeError as exc:
        print(json.dumps({"ok": False, "error": f"[{exc.code}] {exc}"}, ensure_ascii=False))
        return 1
    try:
        report = run_smoke(c.BridgeHttp(info.port, info.password), args.frames, args.timeout)
        report["ios"] = info.ios
    finally:
        bridge.stop()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
