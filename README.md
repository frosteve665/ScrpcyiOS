# ScrpcyiOS

scrcpy-style iPhone mirroring + control for PC. Plug in USB, see the screen, tap/swipe/type from the PC.
Requires iOS 17.4+, Developer Mode, Trust, and "Apple Devices" (or iTunes) on Windows.

```
bridge/ios_bridge.py   GPL-3.0 process (wraps pymobiledevice3: tunnel + CoreDevice display/HID service)
client/                no pymobiledevice3 import; HTTP client, HEVC decode (PyAV), input, player window
tests/                 mock bridge + 26 tests (real HEVC decode path), run: pytest -q tests
```

## Run (PC with iPhone)
```
pip install pymobiledevice3 av pillow
python client/player.py            # add --udid <UDID> if several phones
```
Mouse = finger, right click = Home, keyboard = iPhone hardware keyboard.
Ctrl+A/C/V/X/Z -> Cmd+key (C/V sync clipboard). F1 Home, F2 Lock, F3/F4 Volume, F5 screenshot, F6/F7 rotate, F8 restart.

## Status (honest)
- Verified: client, framing, HEVC decode, input queue, key mapping, error mapping (mock tests).
- NOT yet verified on a real iPhone: tunnel start, video latency, touch/button/key delivery.
- Single finger only. Second pointer is ignored until multitouch is proven (`InputController(multitouch=True)`).
- TODO: Wi-Fi transport, audio playback (bridge supports /audio.bin), recording, multitouch probe, packaging.

## License
`bridge/` is GPL-3.0 (imports pymobiledevice3). Keep `client/` free of pymobiledevice3 imports.
