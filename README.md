# ScrpcyiOS

iPhone engine for StrikeMirror: scrcpy-style mirroring and control of an iPhone from a Windows PC over USB.
Requires Developer Mode, Trust, and "Apple Devices" (or iTunes) on Windows. **Minimum iOS version is not yet established** (sources disagree: 17.4 vs 27; see `docs/RESEARCH.md` #4). Sprint 0 measures it.

```
bridge/    GPL-3.0 process wrapping pymobiledevice3 (tunnel + CoreDevice display/HID). Talks HTTP on 127.0.0.1.
client/    No pymobiledevice3 import. HTTP client, HEVC decode (PyAV), input, recovery policy, Tk player.
tools/     smoke_test.py (hardware smoke test), see bridge/multitouch_probe.py for the 2-finger experiment.
tests/     68 tests: mock bridge with real HEVC, framing, recovery, HID report layout, pymobiledevice3 API contract.
docs/      PROTOCOL.md, SPRINTS.md, RESEARCH.md, DEVICE-MATRIX.md, superpowers/specs/ (design with flowcharts + ERD)
```

## Try it (PC + iPhone)
```
pip install -r requirements.txt
python client/player.py              # window: mouse = finger, right click = Home, F1..F9 shortcuts (F9 = record MP4, needs PyAV with libx265 + numpy)
python client/player.py --help       # scrcpy-style options: --no-audio, --audio-buffer MS, --no-clipboard-autosync, --record FILE, -f
python tools/smoke_test.py           # prints a JSON report, exit code 0 = pass
```

## Status (honest)
- Verified by automated tests (no iPhone): framing, HEVC decode path, input queue, key mapping, error mapping, HID report layout (byte-identical to pymobiledevice3), recovery policy, auto-reconnect, MP4 recording (remux, no re-encode), scroll-wheel swipe, API contract, audio playout buffer + `/audio.bin` client, two-way clipboard sync.
- **Not verified on a real iPhone:** tunnel, video latency, touch/button/key delivery, audio, clipboard, rotation.
- Touch is one finger. Second pointer is ignored until `bridge/multitouch_probe.py` proves more.
- Plan: `docs/SPRINTS.md`. Design: `docs/superpowers/specs/2026-10-21-ios-engine-design.md` (draft, awaiting owner review).

## License
`bridge/` is GPL-3.0 (imports pymobiledevice3). Keep `client/` free of pymobiledevice3 imports. Legal review of the process boundary is planned before any commercial release.
