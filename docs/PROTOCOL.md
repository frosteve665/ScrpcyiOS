# Bridge protocol v1

Contract between a client (StrikeMirror's iOS backend, `client/ios_client.py`) and `bridge/ios_bridge.py`.
Status per item: **verified** = read from pymobiledevice3 11.40.2 source and exercised by the mock tests; **unverified** = needs a real iPhone.

## 1. Launch and events (stdout, one JSON object per line)

```
bridge/ios_bridge.py [--udid UDID] [--port N] [--exit-on-stdin-eof] [--log-file PATH | --no-log] [--no-audio] [--no-motion-idr] [--max-bitrate KBPS]
```

| event | fields | meaning |
|-------|--------|---------|
| `status` | `phase` = detecting, checking, tunnel, mounting, stopped; optional `note`, `udid` | progress for a wizard UI |
| `ready` | `port`, `password`, `udid`, `ios` | HTTP server is up on `127.0.0.1:port` |
| `error` | `code`, `message` | fatal; process exits with code 2 |

Error codes: `driver_missing`, `device_not_found`, `multiple_devices`, `not_trusted`, `unsupported_ios`, `developer_mode_off`, `display_service_missing`, `port_in_use`, `unknown`. Client-side codes: `bridge_exited`, `timeout`, `stream_not_ready`, `stream_http`, `bad_frame`.
`STRIKEMIRROR_BRIDGE_PASSWORD` may supply the password; otherwise a random one is generated. 
**Lifecycle (verified by tests with a fake bridge; unplug/replug unverified on a device).**
- `--exit-on-stdin-eof`: the client spawns the bridge with a stdin pipe and the bridge exits (status `stopped`, `reason: parent_gone`) when that pipe closes. This is how an orphaned bridge dies on every OS, including when the client crashes. A client stops the bridge by closing stdin first, then SIGTERM/terminate after about 1.5 s.
- `BridgeSupervisor` (client) respawns a dead bridge with exponential backoff (`ReconnectPolicy`, resets after 10 s alive) and rebinds `BridgeHttp` to the new port and password. Events it reports: `bridge_down`, `restarted`, `restart_failed` (with `code`), `gave_up`.
- Logs go to a rotating file (default `%LOCALAPPDATA%/StrikeMirror/logs/bridge.log`, 2 MB x 3 backups), never to stdout.
- Touch coordinates are normalised over the frame buffer; after a rotation iOS re-renders the buffer upright, so no rotation transform is applied (derived from pymobiledevice3 `viewer.js`; **unverified on a device**).
- Under fast motion the encoder can shrink the picture into the top-left of the buffer with flat gray padding; clients should detect and stretch it back (`client/frame_fix.py`).

## 2. HTTP (all endpoints)

- Bind: `127.0.0.1` only. Auth: HTTP Basic, any user name, password from `ready`. Missing/wrong password -> `401`.
- If an `Origin` header is present it must equal the `Host` (blocks other web pages). Non-browser clients send none. **verified**
- Touch/button/key POSTs are queued and answered `200 ok` immediately (fire and forget); ordering is preserved. **verified**

## 3. Video

`GET /codec` -> `200 {"codec":"hev1....","description":"<base64 hvcC>"}`; `503` while the stream is starting (retry every ~0.5 s). **verified**

`GET /stream.bin` -> chunked body of frames: **verified**

```
u32 BE  L        (= 1 + payload size)
u8      type     0 = key, 1 = delta, 2 = key that requires a decoder reset
bytes   payload  one access unit as 4-byte-BE-length-prefixed NAL units (hvcC sample format)
```

Rules for clients: configure the decoder with the hvcC from `/codec`; drop deltas until the first key/reset frame; rebuild the decoder on type 2; reject `L < 1` or absurd `L`. The first frame sent to a new subscriber is the cached last keyframe. Video codec is HEVC only.

Recovery (see design D3): `POST /pli` -> `202` (requests a keyframe; the device may ignore it; max 2 per 10 s), then `POST /restart` -> `202` (restarts the device stream; max once per 10 s). **verified (server side), device behaviour unverified**

## 4. Input

Coordinates are `0..65535` across the device screen (`x` left to right, `y` top to bottom).

| endpoint | body | notes |
|----------|------|-------|
| `POST /touch` | `{"type":"contact"\|"release"\|"tap","x":int,"y":int,"id":int?}` | A drag is repeated `contact` then one `release`. **The bridge supports ONE contact; `id` is accepted and ignored (reserved for multitouch).** Rotated-screen coordinate mapping: **unverified**. First tap after stream start may be dropped (0.3-0.5 s HID auth warm-up). |
| `POST /button` | `{"name":"home"\|"lock"\|"volume-up"\|"volume-down"\|"mute"\|"siri","state":"press"\|"down"\|"up"}` | `press` holds ~0.5 s for lock, ~1 s for siri. |
| `POST /key` | `{"usages":[HID keyboard usage ints]}` | The full set of keys currently held; `[]` releases all. Ctrl+A/C/V/X/Z -> add usage `0xE3` (Cmd). |
| `POST /rotate` | `{"direction":"left"\|"right"}` | returns the device orientation state as JSON |

## 5. Clipboard

`GET /clipboard` -> `{"text":str|null,"image":{...}|null}`; `POST /clipboard` `{"text":str}` -> `{"ok":true}`; `GET|POST /clipboard/image` (PNG). **verified (code)**, **unverified (device)**.

`GET /clipboard/events[?since=SEQ]` long-polls up to 25 s and returns `{"seq":int,"text":str|null,"image":{...}|null}`. Without `since` (first poll) it answers at once with the current `seq` and `text: null`: what the device held before sync started is never reported. With `since` equal to the current `seq` it waits for a device-side copy; `text` is non-null only when `seq` changed. **verified (source)**, **unverified (device)**.

Client (`ClipboardSync`, on by default in the player, `--no-clipboard-autosync` turns it off): device copies go to the PC clipboard; PC clipboard changes (polled every 500 ms) are POSTed. The last synced text is remembered on both sides, so neither direction echoes. Text only; images are not synced yet.

## 6. Audio

`GET /audio.bin` -> chunked body of messages `[u32 BE len][len bytes of s16le 48 kHz stereo PCM]` (one decoded 10 ms AAC-ELD packet per message, ~192 KB/s). **verified (pymobiledevice3 11.40.2 source `screen_stream.py` + `viewer.js`; device untested)**. `503` when audio is unavailable (bridge started with `--no-audio`, or decoder dependencies missing): clients must not retry. `500` if the device audio session fails to start. A slow subscriber loses the oldest packets (server queue of 64). The bridge keeps the device audio session alive after the last subscriber leaves, so reconnecting is cheap.

Client (`AudioSession`): pre-buffers `--audio-buffer` ms (default 50, like scrcpy) in a `JitterBuffer`, plays silence on underrun and re-buffers, drops the oldest audio past `2 x buffer + 100` ms. States: `connecting`, `playing`, `unavailable` (503), `no_output` (no sound card / PortAudio), `stopped`. Audio/video sync and drift: **unverified**.

## 7. Versioning

This is v1. New fields may be added; clients must ignore unknown fields. Breaking changes bump the major version and are announced in `status` / `ready` (field `protocol`, implemented; the client refuses a bridge with a newer major version).
