"""StrikeMirror iOS client. MIT-style code; must NOT import pymobiledevice3.

Talks to bridge/ios_bridge.py (a separate GPL process) over local HTTP.
"""
from __future__ import annotations

import base64
import http.client
import json
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

FRAME_KEY, FRAME_DELTA, FRAME_KEY_RESET = 0, 1, 2
TOUCH_MAX = 65535


# --------------------------------------------------------------------------- bridge process
class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class BridgeInfo:
    port: int
    password: str
    udid: str
    ios: str


class BridgeProcess:
    """Starts the bridge subprocess and waits for its 'ready' (or 'error') event."""

    def __init__(self, udid: Optional[str] = None, python: Optional[str] = None,
                 bridge_path: Optional[Path] = None, extra_args: Optional[list[str]] = None,
                 on_status: Optional[Callable[[dict], None]] = None):
        self.cmd = [python or sys.executable,
                    str(bridge_path or Path(__file__).resolve().parent.parent / "bridge" / "ios_bridge.py")]
        if udid:
            self.cmd += ["--udid", udid]
        self.cmd += extra_args or []
        self.on_status = on_status
        self.proc: Optional[subprocess.Popen] = None
        self.info: Optional[BridgeInfo] = None

    def start(self, timeout: float = 120.0) -> BridgeInfo:
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        deadline = time.monotonic() + timeout
        result: "queue.Queue[dict]" = queue.Queue()

        def pump() -> None:
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                try:
                    result.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            result.put({"event": "eof"})

        threading.Thread(target=pump, daemon=True).start()
        while time.monotonic() < deadline:
            try:
                ev = result.get(timeout=0.5)
            except queue.Empty:
                continue
            kind = ev.get("event")
            if kind == "status" and self.on_status:
                self.on_status(ev)
            elif kind == "ready":
                self.info = BridgeInfo(ev["port"], ev["password"], ev.get("udid", ""), ev.get("ios", ""))
                return self.info
            elif kind == "error":
                self.stop()
                raise BridgeError(ev.get("code", "unknown"), ev.get("message", ""))
            elif kind == "eof":
                raise BridgeError("bridge_exited", "Bridge berhenti sebelum siap.")
        self.stop()
        raise BridgeError("timeout", "Bridge tidak siap tepat waktu.")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# --------------------------------------------------------------------------- HTTP
def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f":{password}".encode()).decode()


class BridgeHttp:
    def __init__(self, port: int, password: str, host: str = "127.0.0.1"):
        self.host, self.port, self.auth = host, port, _basic(password)
        self._local = threading.local()

    def _conn(self, timeout: float = 10.0) -> http.client.HTTPConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
            self._local.conn = conn
        return conn

    def request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 10.0,
                keep_alive: bool = False) -> tuple[int, bytes]:
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        headers = {"Authorization": self.auth}
        if data is not None:
            headers["Content-Type"] = "application/json"
        conn = self._conn(timeout) if keep_alive else http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, payload
        except (OSError, http.client.HTTPException):
            if keep_alive:
                self._local.conn = None
            raise
        finally:
            if not keep_alive:
                conn.close()

    def codec(self, retries: int = 20, delay: float = 0.5) -> tuple[str, bytes]:
        """Wait for the stream to come up; returns (codec string, hvcC bytes)."""
        for _ in range(retries):
            status, payload = self.request("GET", "/codec", timeout=15.0)
            if status == 200:
                d = json.loads(payload)
                return d["codec"], base64.b64decode(d["description"])
            time.sleep(delay)
        raise BridgeError("stream_not_ready", "Stream video belum siap.")

    def open_stream(self) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=30.0)
        conn.request("GET", "/stream.bin", headers={"Authorization": self.auth})
        resp = conn.getresponse()
        if resp.status != 200:
            body = resp.read()[:200]
            conn.close()
            raise BridgeError("stream_http", f"/stream.bin -> {resp.status} {body!r}")
        resp._strikemirror_conn = conn  # keep alive for the response's lifetime
        return resp


# --------------------------------------------------------------------------- framing
def iter_frames(read: Callable[[int], bytes]) -> Iterator[tuple[int, bytes]]:
    """Parse [4B BE len][1B type][len-1 bytes of 4-byte-length-prefixed NALUs]."""
    def exact(n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            chunk = read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    while True:
        head = exact(4)
        if head is None:
            return
        length = int.from_bytes(head, "big")
        if length < 1 or length > 64 * 1024 * 1024:
            raise BridgeError("bad_frame", f"Panjang frame tidak masuk akal: {length}")
        body = exact(length)
        if body is None:
            return
        yield body[0], body[1:]


# --------------------------------------------------------------------------- decoding
class HevcDecoder:
    """HEVC (hvcC) decoder on PyAV. Frames arrive as length-prefixed NALUs."""

    def __init__(self, hvcc: bytes):
        self.hvcc = hvcc
        self._make()

    def _make(self) -> None:
        import av
        self._av = av
        self.ctx = av.CodecContext.create("hevc", "r")
        self.ctx.extradata = self.hvcc
        self.ctx.thread_type = "SLICE"  # low latency: no frame-level threading delay
        self.ctx.open()

    def reset(self) -> None:
        self._make()

    def decode(self, data: bytes):
        """Return a list of decoded av.VideoFrame (usually 0 or 1)."""
        pkt = self._av.Packet(data)
        return list(self.ctx.decode(pkt))


class RecoveryPolicy:
    """Decides how to react to decode errors without hammering the iPhone's encoder.

    Evidence: the device may ignore PLI/FIR after the first IDR (rPlayHub notes) and a PLI barrage
    can corrupt the encoder on iOS 26/27 (pymobiledevice3 source comments). So: at most `max_pli`
    keyframe requests spaced `pli_interval` apart, then a session restart, itself rate-limited.
    """

    def __init__(self, pli_interval: float = 2.0, max_pli: int = 2, restart_interval: float = 10.0):
        self.pli_interval, self.max_pli, self.restart_interval = pli_interval, max_pli, restart_interval
        self._plis = 0
        self._last_action: Optional[float] = None
        self._last_restart = float("-inf")

    def on_error(self, now: float) -> str:
        if self._last_action is not None and now - self._last_action < self.pli_interval:
            return "wait"
        if self._plis < self.max_pli:
            self._plis += 1
            self._last_action = now
            return "pli"
        if now - self._last_restart >= self.restart_interval:
            self._plis = 0
            self._last_restart = self._last_action = now
            return "restart"
        return "wait"

    def on_healthy(self) -> None:
        self._plis = 0


class VideoSession:
    """Pulls /stream.bin, decodes, and keeps the newest frame (drops stale ones)."""

    def __init__(self, http_api: BridgeHttp):
        self.api = http_api
        self.latest = None
        self.frames = 0
        self.last_error: Optional[str] = None
        self.policy = RecoveryPolicy()
        self._good = 0
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="video")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait_frame(self, last: int, timeout: float = 1.0):
        with self._cond:
            if self.frames == last:
                self._cond.wait(timeout)
            return self.frames, self.latest

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                _, hvcc = self.api.codec()
                dec = HevcDecoder(hvcc)
                resp = self.api.open_stream()
                backoff = 0.5
                need_key = True
                for kind, data in iter_frames(resp.read1 if hasattr(resp, "read1") else resp.read):
                    if self._stop.is_set():
                        return
                    if kind == FRAME_KEY_RESET:
                        dec.reset()
                        need_key = False
                    elif kind == FRAME_KEY:
                        need_key = False
                    elif need_key:
                        continue
                    try:
                        for f in dec.decode(data):
                            with self._cond:
                                self.latest, self.frames = f, self.frames + 1
                                self._cond.notify_all()
                            self._good += 1
                            if self._good >= 30:
                                self.policy.on_healthy()
                                self._good = 0
                    except Exception as exc:  # decoder error: rate-limited recovery
                        self.last_error = str(exc)
                        self._good = 0
                        need_key = True
                        dec.reset()
                        action = self.policy.on_error(time.monotonic())
                        if action != "wait":
                            try:
                                self.api.request("POST", "/pli" if action == "pli" else "/restart")
                            except OSError:
                                pass
            except (OSError, http.client.HTTPException, BridgeError) as exc:
                self.last_error = str(exc)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 5.0)


# --------------------------------------------------------------------------- input
BUTTONS = ("home", "lock", "volume-up", "volume-down", "mute", "siri")


def to_touch_coord(v: float) -> int:
    return max(0, min(TOUCH_MAX, int(round(v * TOUCH_MAX))))


class InputController:
    """Sends touch/button/key events.

    Touch is pointer-id aware. The iOS HID path proven in code supports ONE contact,
    so while `multitouch` is False a second pointer is ignored (first finger wins).
    Set multitouch=True only after tools/multitouch_probe.py proves 2 contacts work.
    Moves are coalesced: only the newest position is sent when the link is slower than input.
    """

    def __init__(self, api: BridgeHttp, multitouch: bool = False):
        self.api, self.multitouch = api, multitouch
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._active: Optional[int] = None
        self._lock = threading.Lock()
        self._pending_move: Optional[dict] = None
        self._stop = threading.Event()
        threading.Thread(target=self._pump, daemon=True, name="input").start()

    # coordinates are normalized 0..1 over the video frame
    def down(self, pid: int, x: float, y: float) -> bool:
        with self._lock:
            if self._active is not None and not self.multitouch:
                return False
            self._active = pid
            self._pending_move = None
        self._q.put(("/touch", {"type": "contact", "x": to_touch_coord(x), "y": to_touch_coord(y), "id": pid}))
        return True

    def move(self, pid: int, x: float, y: float) -> None:
        with self._lock:
            if self._active != pid:
                return
            first = self._pending_move is None
            self._pending_move = {"type": "contact", "x": to_touch_coord(x), "y": to_touch_coord(y), "id": pid}
        if first:
            self._q.put(("MOVE", {}))

    def up(self, pid: int, x: float, y: float) -> None:
        with self._lock:
            if self._active != pid:
                return
            self._active = None
            self._pending_move = None
        self._q.put(("/touch", {"type": "release", "x": to_touch_coord(x), "y": to_touch_coord(y), "id": pid}))

    def tap(self, x: float, y: float) -> None:
        self.down(0, x, y)
        time.sleep(0.04)
        self.up(0, x, y)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, ms: int = 250, steps: int = 12) -> None:
        if not self.down(0, x1, y1):
            return
        for i in range(1, steps + 1):
            t = i / steps
            self.move(0, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            time.sleep(ms / 1000 / steps)
        self.up(0, x2, y2)

    def button(self, name: str, state: str = "press") -> None:
        if name not in BUTTONS:
            raise ValueError(f"tombol tidak dikenal: {name}")
        self._q.put(("/button", {"name": name, "state": state}))

    def keys(self, usages: list[int]) -> None:
        self._q.put(("/key", {"usages": list(usages)}))

    def _pump(self) -> None:
        while not self._stop.is_set():
            path, body = self._q.get()
            if path == "MOVE":
                with self._lock:
                    body, self._pending_move = self._pending_move, None
                if body is None:
                    continue
                path = "/touch"
            try:
                self.api.request("POST", path, body, timeout=5.0, keep_alive=True)
            except (OSError, http.client.HTTPException):
                time.sleep(0.05)

    def close(self) -> None:
        self._stop.set()
        self._q.put(("MOVE", {}))


# --------------------------------------------------------------------------- facade
class IOSDevice:
    """High level: start bridge, video, input, clipboard, rotate, screenshot."""

    def __init__(self, udid: Optional[str] = None, on_status: Optional[Callable[[dict], None]] = None,
                 extra_args: Optional[list[str]] = None):
        self.bridge = BridgeProcess(udid, on_status=on_status, extra_args=extra_args)
        self.api: Optional[BridgeHttp] = None
        self.video: Optional[VideoSession] = None
        self.input: Optional[InputController] = None

    def connect(self) -> BridgeInfo:
        info = self.bridge.start()
        self.api = BridgeHttp(info.port, info.password)
        self.video = VideoSession(self.api)
        self.input = InputController(self.api)
        self.video.start()
        return info

    def clipboard_get(self) -> dict:
        status, payload = self.api.request("GET", "/clipboard")
        return json.loads(payload) if status == 200 else {}

    def clipboard_set(self, text: str) -> bool:
        status, _ = self.api.request("POST", "/clipboard", {"text": text})
        return status == 200

    def rotate(self, direction: str = "right") -> dict:
        status, payload = self.api.request("POST", "/rotate", {"direction": direction}, timeout=20.0)
        return json.loads(payload) if status == 200 else {}

    def restart_stream(self) -> None:
        self.api.request("POST", "/restart")

    def screenshot(self, path: str) -> bool:
        _, frame = self.video.wait_frame(0, 0.0)
        if frame is None:
            return False
        frame.to_image().save(path)  # needs Pillow
        return True

    def close(self) -> None:
        if self.input:
            self.input.close()
        if self.video:
            self.video.stop()
        self.bridge.stop()
