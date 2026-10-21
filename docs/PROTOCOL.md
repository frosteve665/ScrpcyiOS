# Bridge protocol v1

Contract between a client (StrikeMirror's iOS backend, `client/ios_client.py`) and `bridge/ios_bridge.py`.
Status per item: **verified** = read from pymobiledevice3 11.40.2 source and exercised by the mock tests; **unverified** = needs a real iPhone.

## 1. Launch and events (stdout, one JSON object per line)

```
bridge/ios_bridge.py [--udid UDID] [--port N] [--no-audio] [--no-motion-idr] [--max-bitrate KBPS]
```

| event | fields | meaning |
|-------|--------|---------|
| `status` | `phase` = detecting, checking, tunnel, mounting, stopped; optional `note`, `udid` | progress for a wizard UI |
| `ready` | `port`, `password`, `udid`, `ios` | HTTP server is up on `127.0.0.1:port` |
| `error` | `code`, `message` | fatal; process exits with code 2 |

Error codes: `driver_missing`, `device_not_found`, `multiple_devices`, `not_trusted`, `unsupported_ios`, `developer_mode_off`, `display_service_missing`, `unknown`. Client-side codes: `bridge_exited`, `timeout`, `stream_not_ready`, `stream_http`, `bad_frame`.
`STRIKEMIRROR_BRIDGE_PASSWORD` may supply the password; otherwise a random one is generated. Stop the bridge with SIGTERM/terminate.

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

`GET /clipboard` -> `{"text":str|null,"image":{...}|null}`; `POST /clipboard` `{"text":str}` -> `{"ok":true}`; `GET|POST /clipboard/image` (PNG); `GET /clipboard/events` long-poll for device-side copies. **verified (code)**, **unverified (device)**.

## 6. Audio

`GET /audio.bin` -> chunked audio chunks; `503` if the optional audio dependencies are missing. The chunk format (PCM per source comments) and sync with video are **unverified**; to be pinned down in Sprint 4. Disabled by default with `--no-audio`.

## 7. Versioning

This is v1. New fields may be added; clients must ignore unknown fields. Breaking changes bump the major version and are announced in `status` / `ready` (field `protocol`, implemented; the client refuses a bridge with a newer major version).
