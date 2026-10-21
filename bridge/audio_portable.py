"""AAC-ELD audio on Windows/Linux for the bridge (GPL side: it patches pymobiledevice3).

pymobiledevice3 decodes the iPhone's AAC-ELD audio with macOS AudioToolbox only, so on Windows
/audio.bin answers 503. FFmpeg's native (float) AAC decoder implements ER AAC ELD including
480-sample frames (libavcodec/aac/aacdec.c: frame_length_short, ff_swb_offset_480), so we swap in
a PyAV decoder with the same interface: decode(one AU) -> s16le 48 kHz stereo PCM bytes.

The cookie captured from the device (F8 E6 40 00) has frameLengthFlag = 0 (512-sample frames),
yet pymobiledevice3 has to force 480 samples per packet to get clean output, so the stream is
480-sample ELD. We therefore configure FFmpeg with the same config but frameLengthFlag = 1.
UNVERIFIED on a real iPhone: if audio is garbled, try the device cookie (ASC_FROM_DEVICE).
"""
from __future__ import annotations

import sys

import av

ASC_FROM_DEVICE = bytes([0xF8, 0xE6, 0x40, 0x00])
ELD_ASC_48K_STEREO_480 = bytes([0xF8, 0xE6, 0x50, 0x00])   # same, with frameLengthFlag = 1


class PortableAacDecoder:
    """One AAC access unit in, interleaved s16le 48 kHz stereo PCM out."""

    def __init__(self, asc: bytes = ELD_ASC_48K_STEREO_480):
        self.asc = asc
        self._ctx = av.CodecContext.create("aac", "r")
        self._ctx.extradata = asc
        self._ctx.open()
        self._resampler = av.AudioResampler(format="s16", layout="stereo", rate=48000)

    def decode(self, au: bytes) -> bytes:
        out = []
        for frame in self._ctx.decode(av.Packet(au)):
            for f in self._resampler.resample(frame):
                out.append(f.to_ndarray().tobytes())
        return b"".join(out)


def enable_portable_audio(screen_stream_module, platform: str = sys.platform) -> bool:
    """Patch pymobiledevice3's screen_stream to decode audio with FFmpeg off macOS. Returns True if patched."""
    if platform == "darwin":
        return False
    screen_stream_module.AACELDDecoder = lambda cookie=ASC_FROM_DEVICE: PortableAacDecoder(ELD_ASC_48K_STEREO_480)
    screen_stream_module.ScreenStreamServer._missing_audio_deps = staticmethod(lambda: [])
    return True
