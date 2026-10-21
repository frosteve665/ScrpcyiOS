"""Fake of the pymobiledevice3 ScreenStreamServer HTTP protocol, for tests without an iPhone."""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import av

PASSWORD = "secret"


def load_hevc(path: str):
    """Return (hvcC bytes, [(is_key, length-prefixed AU bytes)])."""
    with av.open(path) as c:
        s = c.streams.video[0]
        hvcc = bytes(s.codec_context.extradata)
        aus = [(pkt.is_keyframe, bytes(pkt)) for pkt in c.demux(s) if pkt.size]
    return hvcc, aus


def frame_msg(kind: int, data: bytes) -> bytes:
    return (len(data) + 1).to_bytes(4, "big") + bytes([kind]) + data


class MockBridge:
    def __init__(self, mp4: str):
        self.hvcc, self.aus = load_hevc(mp4)
        self.posts: list[tuple[str, dict]] = []
        self.hits: list[str] = []
        self.codec_503_first = 0
        self.clip = "hello"
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def _auth(self) -> bool:
                import base64 as b
                h = self.headers.get("Authorization", "")
                ok = h.startswith("Basic ") and b.b64decode(h[6:]).decode().partition(":")[2] == PASSWORD
                if not ok:
                    self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers()
                return ok

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._auth():
                    return
                outer.hits.append(self.path)
                if self.path == "/codec":
                    if outer.codec_503_first > 0:
                        outer.codec_503_first -= 1
                        return self._send(503)
                    return self._send(200, json.dumps({"codec": "hev1.1.6.L93.B0",
                                                       "description": base64.b64encode(outer.hvcc).decode()}).encode())
                if self.path == "/clipboard":
                    return self._send(200, json.dumps({"text": outer.clip, "image": None}).encode())
                if self.path == "/stream.bin":
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for i, (key, au) in enumerate(outer.aus):
                        msg = frame_msg(2 if i == 0 else (0 if key else 1), au)
                        self.wfile.write(f"{len(msg):x}\r\n".encode() + msg + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    self.close_connection = True
                    return
                self._send(404)

            def do_POST(self):
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    body = {}
                outer.posts.append((self.path, body))
                if self.path == "/clipboard":
                    outer.clip = body.get("text", "")
                    return self._send(200, b'{"ok":true}')
                if self.path == "/rotate":
                    return self._send(200, b'{"orientation":"landscape"}')
                self._send(200, b"ok", "text/plain")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
