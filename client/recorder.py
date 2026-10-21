"""Record the iPhone stream to mp4 by remuxing HEVC access units (no re-encode, no quality loss)."""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

FRAME_DELTA = 1
TIME_BASE = Fraction(1, 90000)
MIN_STEP = 375   # ~4 ms: smaller steps collapse to equal dts after the muxer rescales its timebase


class Mp4Recorder:
    def __init__(self, path: str, hvcc: bytes, width: int, height: int):
        import av
        self._av = av
        # PyAV cannot mux hand-made packets into an encoder-backed stream (sizes collapse to 1 byte),
        # so borrow a stream template from a throwaway 1-frame encode, then install the real hvcC.
        self._template = self._make_template(width, height)
        self._out = av.open(path, "w", format="mp4")
        self._stream = self._out.add_stream_from_template(self._template.streams.video[0])
        self._stream.codec_context.extradata = hvcc
        self._started = False
        self._origin: Optional[float] = None
        self._last_ts = -1

    def _make_template(self, width: int, height: int):
        import io

        import numpy as np
        buf = io.BytesIO()
        with self._av.open(buf, "w", format="mp4") as c:
            s = c.add_stream("libx265", rate=30)
            s.width, s.height, s.pix_fmt = width, height, "yuv420p"
            s.options = {"x265-params": "log-level=none:frame-threads=1:bframes=0"}
            frame = self._av.VideoFrame.from_ndarray(np.zeros((height, width, 3), np.uint8), format="rgb24")
            for p in s.encode(frame):
                c.mux(p)
            for p in s.encode():
                c.mux(p)
        buf.seek(0)
        return self._av.open(buf)

    def write(self, kind: int, data: bytes, t: float) -> bool:
        """Append one access unit. Deltas before the first keyframe are dropped. Returns True if written."""
        if not self._started:
            if kind == FRAME_DELTA:
                return False
            self._started, self._origin = True, t
        ts = max(int((t - self._origin) * 90000), self._last_ts + MIN_STEP)   # strictly increasing
        self._last_ts = ts
        pkt = self._av.Packet(data)
        pkt.stream = self._stream
        pkt.pts = pkt.dts = ts
        pkt.time_base = TIME_BASE
        pkt.is_keyframe = kind != FRAME_DELTA
        self._out.mux(pkt)
        return True

    def close(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None
            self._template.close()
