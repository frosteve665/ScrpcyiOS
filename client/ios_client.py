"""StrikeMirror iOS client. MIT-style code; must NOT import pymobiledevice3.

Talks to bridge/ios_bridge.py (a separate GPL process) over local HTTP.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
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
SUPPORTED_PROTOCOL = 1


# --------------------------------------------------------------------------- bridge process
class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status = status  # HTTP status, when the error came from a response


@dataclass
class BridgeInfo:
    port: int
    password: str
    udid: str
    ios: str


BRIDGE_EXE = "strikemirror-bridge.exe" if sys.platform == "win32" else "strikemirror-bridge"


def default_bridge_cmd() -> list[str]:
    """How to launch the bridge: $STRIKEMIRROR_BRIDGE, else the frozen sibling exe, else the script."""
    override = os.environ.get("STRIKEMIRROR_BRIDGE")
    if override:
        return [override]
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for cand in (here.parent / "strikemirror-bridge" / BRIDGE_EXE, here / BRIDGE_EXE):
            if cand.exists():
                return [str(cand)]
        raise BridgeError("bridge_missing", f"{BRIDGE_EXE} tidak ditemukan di samping player.")
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "bridge" / "ios_bridge.py")]


class BridgeProcess:
    """Starts the bridge subprocess and waits for its 'ready' (or 'error') event."""

    def __init__(self, udid: Optional[str] = None, python: Optional[str] = None,
                 bridge_path: Optional[Path] = None, extra_args: Optional[list[str]] = None,
                 on_status: Optional[Callable[[dict], None]] = None):
        if python or bridge_path:
            self.cmd = [python or sys.executable,
                        str(bridge_path or Path(__file__).resolve().parent.parent / "bridge" / "ios_bridge.py")]
        else:
            self.cmd = default_bridge_cmd()
        if udid:
            self.cmd += ["--udid", udid]
        self.cmd += extra_args or []
        self.cmd.append("--exit-on-stdin-eof")  # the bridge dies with us (stdin is a pipe we own)
        self.on_status = on_status
        self.proc: Optional[subprocess.Popen] = None
        self.info: Optional[BridgeInfo] = None
        self._lock = threading.Lock()
        self._closed = False

    def start(self, timeout: float = 120.0) -> BridgeInfo:
        with self._lock:
            if self._closed:
                raise BridgeError("closed", "Bridge sudah ditutup.")
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            info = self._await_ready(timeout)
        except BridgeError:
            if self._closed:
                raise BridgeError("closed", "Bridge ditutup saat sedang dimulai.") from None
            raise
        with self._lock:
            if not self._closed:
                return info
        self.stop()
        raise BridgeError("closed", "Bridge ditutup saat sedang dimulai.")

    def _await_ready(self, timeout: float) -> BridgeInfo:
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
                if ev.get("protocol", 1) > SUPPORTED_PROTOCOL:
                    self.stop()
                    raise BridgeError("protocol_mismatch",
                                      f"Bridge protocol {ev['protocol']} lebih baru dari klien ({SUPPORTED_PROTOCOL}). Perbarui klien.")
                self.info = BridgeInfo(ev["port"], ev["password"], ev.get("udid", ""), ev.get("ios", ""))
                return self.info
            elif kind == "error":
                self.stop()
                raise BridgeError(ev.get("code", "unknown"), ev.get("message", ""))
            elif kind == "eof":
                self.stop()  # reap the exited process
                raise BridgeError("bridge_exited", "Bridge berhenti sebelum siap.")
        self.stop()
        raise BridgeError("timeout", "Bridge tidak siap tepat waktu.")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def close(self) -> None:
        """Stop for good: a start() in progress is aborted and later starts are refused."""
        with self._lock:
            self._closed = True
        self.stop()

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
        if proc is None:
            return
        if proc.stdin:
            try:
                proc.stdin.close()          # graceful: the bridge exits on stdin EOF
            except OSError:
                pass
        if proc.poll() is None:
            try:
                proc.wait(1.5)
                return
            except subprocess.TimeoutExpired:
                pass
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()


class BridgeSupervisor:
    """Keeps a bridge alive: respawns it with backoff when it dies (cable pulled, crash).

    The HTTP client is rebound to the new port/password, so VideoSession and InputController
    (which reconnect on their own) pick the new bridge up without being recreated.
    on_event gets dicts: bridge_down, restarted, restart_failed, gave_up.
    """

    STABLE_AFTER = 10.0  # seconds alive before the backoff resets
    # Retrying cannot fix these; the user (or a newer client) must act first.
    FATAL = frozenset({"unsupported_ios", "protocol_mismatch", "port_in_use", "closed"})

    def __init__(self, bridge: BridgeProcess, api: "BridgeHttp", policy: Optional["ReconnectPolicy"] = None,
                 on_event: Optional[Callable[[dict], None]] = None, poll: float = 0.5):
        self.bridge, self.api = bridge, api
        self.policy = policy or ReconnectPolicy()
        self.on_event = on_event or (lambda e: None)
        self.poll = poll
        self.restarts = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._since = 0.0

    def start_initial(self, timeout: float = 120.0) -> BridgeInfo:
        info = self.bridge.start(timeout)
        self.api.rebind(info.port, info.password)
        self._since = time.monotonic()
        self._thread = threading.Thread(target=self._watch, daemon=True, name="bridge-supervisor")
        self._thread.start()
        return info

    def _watch(self) -> None:
        while not self._stop.wait(self.poll):
            if self.bridge.alive():
                continue
            if time.monotonic() - self._since >= self.STABLE_AFTER:
                self.policy.on_success()
            self.on_event({"event": "bridge_down"})
            if not self._respawn():
                return

    def _respawn(self) -> bool:
        while not self._stop.is_set():
            delay = self.policy.next_delay()
            if delay is None:
                self.on_event({"event": "gave_up"})
                return False
            if self._stop.wait(delay):
                return False
            try:
                info = self.bridge.start()
            except BridgeError as exc:
                if self._stop.is_set():
                    return False
                if exc.code in self.FATAL:
                    self.on_event({"event": "gave_up", "code": exc.code})
                    return False
                self.on_event({"event": "restart_failed", "code": exc.code, "message": str(exc)})
                continue
            if self._stop.is_set():   # stop() raced the start: do not leave a bridge behind
                self.bridge.stop()
                return False
            self.api.rebind(info.port, info.password)
            self._since = time.monotonic()
            self.restarts += 1
            self.on_event({"event": "restarted", "port": info.port})
            return True
        return False

    def stop(self) -> None:
        self._stop.set()
        close = getattr(self.bridge, "close", None) or self.bridge.stop
        close()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(5)


# --------------------------------------------------------------------------- HTTP
def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f":{password}".encode()).decode()


class BridgeHttp:
    def __init__(self, port: int, password: str, host: str = "127.0.0.1"):
        self.host = host
        self._target = (port, _basic(password), 0)   # replaced as one tuple: readers never mix old and new
        self._local = threading.local()

    @property
    def port(self) -> int:
        return self._target[0]

    @property
    def auth(self) -> str:
        return self._target[1]

    def rebind(self, port: int, password: str) -> None:
        """Point at a new bridge (after a respawn). Cached keep-alive connections are dropped."""
        self._target = (port, _basic(password), self._target[2] + 1)

    def _conn(self, timeout: float = 10.0) -> http.client.HTTPConnection:
        port, _, gen = self._target
        conn = getattr(self._local, "conn", None)
        if conn is not None and getattr(self._local, "gen", -1) != gen:
            conn.close()
            conn = None
        if conn is None:
            conn = http.client.HTTPConnection(self.host, port, timeout=timeout)
            self._local.conn, self._local.gen = conn, gen
        return conn

    def request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 10.0,
                keep_alive: bool = False) -> tuple[int, bytes]:
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        port, auth, _ = self._target
        headers = {"Authorization": auth}
        if data is not None:
            headers["Content-Type"] = "application/json"
        conn = self._conn(timeout) if keep_alive else http.client.HTTPConnection(self.host, port, timeout=timeout)
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

    def open_stream(self, path: str = "/stream.bin") -> http.client.HTTPResponse:
        port, auth, _ = self._target
        conn = http.client.HTTPConnection(self.host, port, timeout=30.0)
        conn.request("GET", path, headers={"Authorization": auth})
        resp = conn.getresponse()
        if resp.status != 200:
            body = resp.read()[:200]
            conn.close()
            raise BridgeError("stream_http", f"{path} -> {resp.status} {body!r}", status=resp.status)
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


def iter_audio(read: Callable[[int], bytes]) -> Iterator[bytes]:
    """Parse /audio.bin messages: [4B BE len][len bytes of s16le 48 kHz stereo PCM]."""
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
        if length > 16 * 1024 * 1024:
            raise BridgeError("bad_frame", f"Panjang audio tidak masuk akal: {length}")
        body = exact(length)
        if body is None:
            return
        yield body


class JitterBuffer:
    """PCM playout buffer (s16le 48 kHz stereo = 192 bytes per ms), thread-safe.

    Pre-buffers to target_ms before playing (like scrcpy --audio-buffer), returns silence on
    underrun and re-buffers, and drops the oldest audio when latency would exceed max_ms.
    """

    BYTES_PER_MS = 192

    def __init__(self, target_ms: int = 50, max_ms: int = 200):
        self.target = target_ms * self.BYTES_PER_MS
        self.max = max_ms * self.BYTES_PER_MS
        self._buf = bytearray()
        self._playing = False
        self._lock = threading.Lock()
        self.underruns = 0
        self.dropped_bytes = 0

    def push(self, pcm: bytes) -> None:
        with self._lock:
            self._buf += pcm
            if len(self._buf) > self.max:
                drop = len(self._buf) - self.target
                drop -= drop % 4                      # keep stereo s16 frames aligned
                del self._buf[:drop]
                self.dropped_bytes += drop
            if not self._playing and len(self._buf) >= self.target:
                self._playing = True

    def pull(self, n: int) -> bytes:
        with self._lock:
            if not self._playing:
                return bytes(n)
            out = bytes(self._buf[:n])
            del self._buf[:n]
            if len(out) < n:
                self.underruns += 1
                self._playing = False
                out += bytes(n - len(out))
            return out

    def buffered_ms(self) -> float:
        with self._lock:
            return len(self._buf) / self.BYTES_PER_MS


def sounddevice_output(pull: Callable[[int], bytes]):
    """Open the default sound card (PortAudio via sounddevice) and feed it from pull(nbytes).

    Imported lazily: a missing package or PortAudio library raises here and the AudioSession
    reports state "no_output" while video keeps running.
    """
    import sounddevice as sd

    def callback(outdata, frames, _time, _status):
        outdata[:] = pull(frames * 4)

    stream = sd.RawOutputStream(samplerate=48000, channels=2, dtype="int16", blocksize=480,
                                latency="low", callback=callback)
    stream.start()
    return stream


class AudioSession:
    """Plays GET /audio.bin (s16le 48 kHz stereo) through a JitterBuffer, like scrcpy's audio player.

    state: idle, connecting, playing, unavailable (bridge answered 503, e.g. --no-audio or missing
    decoder; not retried), no_output (no usable sound card; not retried), stopped.
    output_factory(pull) returns an object with close(); it is opened once, on the first 200.
    """

    def __init__(self, http_api: BridgeHttp, buffer_ms: int = 50,
                 output_factory: Optional[Callable[[Callable[[int], bytes]], object]] = None):
        self.api = http_api
        self.jitter = JitterBuffer(target_ms=buffer_ms, max_ms=buffer_ms * 2 + 100)
        self.output_factory = output_factory or sounddevice_output
        self.state = "idle"
        self.last_error: Optional[str] = None
        self._output = None
        self._resp = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="audio")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        resp = self._resp
        if resp is not None:   # unblock a read waiting for the next packet
            try:
                resp._strikemirror_conn.sock.shutdown(2)
            except (AttributeError, OSError):
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3)
        out, self._output = self._output, None
        if out is not None:
            out.close()
        self.state = "stopped"

    def _run(self) -> None:
        reconnect = ReconnectPolicy()
        while not self._stop.is_set():
            self.state = "connecting"
            try:
                resp = self.api.open_stream("/audio.bin")
            except BridgeError as exc:
                self.last_error = str(exc)
                if exc.status == 503:
                    self.state = "unavailable"
                    return
            except (OSError, http.client.HTTPException) as exc:
                self.last_error = str(exc)
            else:
                self._resp = resp
                try:
                    if self._output is None:
                        try:
                            self._output = self.output_factory(self.jitter.pull)
                        except Exception as exc:  # no sound card / no PortAudio: audio off, video on
                            self.last_error = f"audio output: {exc}"
                            self.state = "no_output"
                            return
                    self.state = "playing"
                    reconnect.on_success()
                    for pcm in iter_audio(resp.read1 if hasattr(resp, "read1") else resp.read):
                        if self._stop.is_set():
                            return
                        self.jitter.push(pcm)
                except (OSError, ValueError, http.client.HTTPException, BridgeError) as exc:
                    self.last_error = str(exc)
                finally:
                    self._resp = None
                    resp._strikemirror_conn.close()
            if self._stop.wait(reconnect.next_delay()):
                return


class ClipboardSync:
    """Two-way clipboard sync (scrcpy's default behaviour).

    Device -> host: long-polls GET /clipboard/events?since=seq and calls on_device_text(text)
    for each copy made on the iPhone after sync started. Host -> device: push_host_text(text).
    The last text seen on either side is remembered, so neither direction echoes the other.
    """

    def __init__(self, http_api: BridgeHttp, on_device_text: Callable[[str], None], poll_timeout: float = 35.0):
        self.api = http_api
        self.on_device_text = on_device_text
        self.poll_timeout = poll_timeout   # the bridge answers after at most 25 s
        self._last: Optional[str] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="clipboard")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(2)

    def push_host_text(self, text: str) -> bool:
        """Send a host clipboard change to the device; False if it is the text we already synced."""
        with self._lock:
            if text == self._last:
                return False
            self._last = text
        status, _ = self.api.request("POST", "/clipboard", {"text": text})
        return status == 200

    def _run(self) -> None:
        backoff = ReconnectPolicy(base=1.0, cap=30.0)
        since: Optional[int] = None
        while not self._stop.is_set():
            path = "/clipboard/events" if since is None else f"/clipboard/events?since={since}"
            try:
                status, payload = self.api.request("GET", path, timeout=self.poll_timeout)
                if status != 200:
                    raise BridgeError("clipboard_http", f"{path} -> {status}", status=status)
                ev = json.loads(payload)
            except (OSError, ValueError, http.client.HTTPException, BridgeError):
                since = None   # a respawned bridge numbers copies from scratch
                if self._stop.wait(backoff.next_delay()):
                    return
                continue
            if self._stop.is_set():   # a long-poll can outlive stop(); never call back after it
                return
            backoff.on_success()
            text = ev.get("text")
            if since is not None and isinstance(text, str):
                with self._lock:
                    fresh = text != self._last
                    self._last = text
                if fresh:
                    self.on_device_text(text)
            since = ev.get("seq", since)


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


class ReconnectPolicy:
    """Exponential backoff for reconnecting: base, 2x base, ... capped. max_attempts=None means forever."""

    def __init__(self, base: float = 0.5, cap: float = 5.0, max_attempts: Optional[int] = None):
        self.base, self.cap, self.max_attempts = base, cap, max_attempts
        self._attempts = 0

    def next_delay(self) -> Optional[float]:
        if self.max_attempts is not None and self._attempts >= self.max_attempts:
            return None
        delay = min(self.base * (2 ** self._attempts), self.cap)
        self._attempts += 1
        return delay

    def on_success(self) -> None:
        self._attempts = 0


class VideoSession:
    """Pulls /stream.bin, decodes, and keeps the newest frame (drops stale ones)."""

    def __init__(self, http_api: BridgeHttp):
        self.api = http_api
        self.latest = None
        self.frames = 0
        self.last_error: Optional[str] = None
        self.policy = RecoveryPolicy()
        self._rec_path: Optional[str] = None
        self._rec = None
        self._hvcc = b""
        self._good = 0
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="video")
        self._thread.start()

    def start_recording(self, path: str) -> None:
        """Remux the live stream to mp4 (no re-encode). Starts at the next keyframe."""
        self._rec_path = path

    def stop_recording(self) -> None:
        rec, self._rec, self._rec_path = self._rec, None, None
        if rec is not None:
            rec.close()

    def stop(self) -> None:
        self._stop.set()
        self.stop_recording()

    def wait_frame(self, last: int, timeout: float = 1.0):
        with self._cond:
            if self.frames == last:
                self._cond.wait(timeout)
            return self.frames, self.latest

    def _record(self, kind: int, data: bytes, frame) -> None:
        if self._rec_path is None:
            return
        try:
            if self._rec is None:
                if kind == FRAME_DELTA:
                    return
                from recorder import Mp4Recorder
                self._rec = Mp4Recorder(self._rec_path, self._hvcc, frame.width, frame.height)
            self._rec.write(kind, data, time.monotonic())
        except Exception as exc:  # recording must never break mirroring
            self.last_error = f"recording stopped: {exc}"
            self._rec_path = None

    def _run(self) -> None:
        reconnect = ReconnectPolicy()
        while not self._stop.is_set():
            try:
                _, hvcc = self.api.codec()
                self._hvcc = hvcc
                dec = HevcDecoder(hvcc)
                resp = self.api.open_stream()
                reconnect.on_success()
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
                            self._record(kind, data, f)
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
            if self._stop.wait(reconnect.next_delay()):
                return


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
                 extra_args: Optional[list[str]] = None, on_event: Optional[Callable[[dict], None]] = None,
                 audio: bool = False, audio_buffer_ms: int = 50, audio_output=None,
                 on_device_clipboard: Optional[Callable[[str], None]] = None):
        """audio: play /audio.bin (the bridge must run without --no-audio).
        on_device_clipboard: enables two-way clipboard sync; called from a worker thread."""
        self.bridge = BridgeProcess(udid, on_status=on_status, extra_args=extra_args)
        self.on_event = on_event
        self.audio_enabled, self.audio_buffer_ms, self.audio_output = audio, audio_buffer_ms, audio_output
        self.on_device_clipboard = on_device_clipboard
        self.audio: Optional[AudioSession] = None
        self.clipboard: Optional[ClipboardSync] = None
        self.link_state = "ok"   # ok | reconnecting | gave_up (read by the UI)
        self.supervisor: Optional[BridgeSupervisor] = None
        self.api: Optional[BridgeHttp] = None
        self.video: Optional[VideoSession] = None
        self.input: Optional[InputController] = None

    def connect(self) -> BridgeInfo:
        self.api = BridgeHttp(0, "")
        self.supervisor = BridgeSupervisor(self.bridge, self.api, on_event=self._on_supervisor_event)
        info = self.supervisor.start_initial()
        self.video = VideoSession(self.api)
        self.input = InputController(self.api)
        self.video.start()
        self._start_media()
        return info

    def _start_media(self) -> None:
        if self.audio_enabled:
            self.audio = AudioSession(self.api, self.audio_buffer_ms, self.audio_output)
            self.audio.start()
        if self.on_device_clipboard is not None:
            self.clipboard = ClipboardSync(self.api, self.on_device_clipboard)
            self.clipboard.start()

    def _on_supervisor_event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "bridge_down":
            self.link_state = "reconnecting"
            if self.video is not None:   # the old stream's hvcC/timestamps do not carry over
                self.video.stop_recording()
        elif kind == "restarted":
            self.link_state = "ok"
        elif kind == "gave_up":
            self.link_state = "gave_up"
        if self.on_event:
            self.on_event(ev)

    def clipboard_get(self) -> dict:
        status, payload = self.api.request("GET", "/clipboard")
        return json.loads(payload) if status == 200 else {}

    def clipboard_set(self, text: str) -> bool:
        status, _ = self.api.request("POST", "/clipboard", {"text": text})
        return status == 200

    def clipboard_push(self, text: str) -> bool:
        """Host clipboard -> device; with sync on, unchanged text is not resent (returns False)."""
        if self.clipboard is not None:
            return self.clipboard.push_host_text(text)
        return self.clipboard_set(text)

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
        for part in (getattr(self, "audio", None), getattr(self, "clipboard", None)):
            if part is not None:
                part.stop()
        if self.supervisor:
            self.supervisor.stop()
        else:
            self.bridge.stop()
