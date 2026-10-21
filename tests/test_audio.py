"""Audio: portable AAC decoder for the bridge (Windows has no AudioToolbox) and client playback buffer."""
import io
import struct

import av
import numpy as np
import pytest

import audio_portable as ap
import ios_client as c


def aac_lc_aus(seconds=0.5, rate=48000):
    """Encode a 440 Hz tone to raw AAC-LC access units (no ADTS) + its AudioSpecificConfig."""
    enc = av.CodecContext.create("aac", "w")
    enc.sample_rate, enc.layout, enc.format = rate, "stereo", "fltp"
    enc.open()
    n, aus, t = enc.frame_size, [], 0
    total = int(seconds * rate)
    while t < total:
        x = np.sin(2 * np.pi * 440 * (np.arange(t, t + n) / rate)).astype(np.float32) * 0.5
        fr = av.AudioFrame.from_ndarray(np.stack([x, x]), format="fltp", layout="stereo")
        fr.sample_rate, fr.pts = rate, t
        aus += [bytes(p) for p in enc.encode(fr)]
        t += n
    aus += [bytes(p) for p in enc.encode(None)]
    return bytes(enc.extradata), aus


# ---- bridge side ---------------------------------------------------------------------------
def test_portable_decoder_outputs_s16le_stereo_48k():
    asc, aus = aac_lc_aus()
    dec = ap.PortableAacDecoder(asc)
    pcm = b"".join(dec.decode(au) for au in aus)
    assert len(pcm) > 48000 * 4 * 0.3            # at least 0.3 s of 16-bit stereo came out
    samples = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2)
    assert np.abs(samples).max() > 5000          # a real tone, not silence
    assert np.corrcoef(samples[:, 0], samples[:, 1])[0, 1] > 0.99   # lossy codec: L ~= R


def test_portable_decoder_drops_garbage_without_raising_forever():
    asc, aus = aac_lc_aus(0.2)
    dec = ap.PortableAacDecoder(asc)
    with pytest.raises(av.error.FFmpegError):
        dec.decode(b"\x00\x01garbage")
    assert dec.decode(aus[3]) is not None         # still usable afterwards (pmd recreates after 5 errors)


def test_eld_config_signals_480_sample_frames():
    asc = ap.ELD_ASC_48K_STEREO_480
    # 5 bits 31 (escape) + 6 bits 7 -> AOT 39 (ER AAC ELD); 48 kHz; 2 channels; frameLengthFlag = 1
    bits = "".join(f"{b:08b}" for b in asc)
    assert int(bits[0:5], 2) == 31 and 32 + int(bits[5:11], 2) == 39
    assert int(bits[11:15], 2) == 3 and int(bits[15:19], 2) == 2 and bits[19] == "1"


def test_eld_config_is_accepted_by_ffmpeg():
    ap.PortableAacDecoder(ap.ELD_ASC_48K_STEREO_480)   # opens without raising


def test_patch_installs_portable_decoder_into_screen_stream(monkeypatch):
    import types
    mod = types.SimpleNamespace(AACELDDecoder=object, AAC_ELD_ASC_48K_STEREO_480=b"\xf8\xe6\x40\x00")

    class Server:
        @staticmethod
        def _missing_audio_deps():
            return ["macOS (AudioToolbox)"]
    mod.ScreenStreamServer = Server
    assert ap.enable_portable_audio(mod, platform="win32") is True
    assert mod.ScreenStreamServer._missing_audio_deps() == []
    d = mod.AACELDDecoder(mod.AAC_ELD_ASC_48K_STEREO_480)
    assert isinstance(d, ap.PortableAacDecoder) and d.asc == ap.ELD_ASC_48K_STEREO_480


def test_patch_leaves_macos_alone():
    import types
    mod = types.SimpleNamespace(AACELDDecoder=object)
    assert ap.enable_portable_audio(mod, platform="darwin") is False and mod.AACELDDecoder is object


# ---- client side ---------------------------------------------------------------------------
def test_audio_messages_are_parsed_across_split_reads():
    data = struct.pack(">I", 4) + b"AAAA" + struct.pack(">I", 2) + b"BB"
    s = io.BytesIO(data)
    assert list(c.iter_audio(lambda n: s.read(min(n, 3)))) == [b"AAAA", b"BB"]


def test_jitter_buffer_waits_for_target_then_plays_in_order():
    jb = c.JitterBuffer(target_ms=40, max_ms=200)        # 48 kHz stereo s16: 192 bytes per ms
    jb.push(b"\x01" * 192 * 20)
    assert jb.pull(192 * 10) == b"\x00" * 192 * 10      # still pre-buffering: silence
    jb.push(b"\x02" * 192 * 30)
    out = jb.pull(192 * 25)
    assert out == b"\x01" * 192 * 20 + b"\x02" * 192 * 5


def test_jitter_buffer_drops_oldest_when_latency_grows():
    jb = c.JitterBuffer(target_ms=40, max_ms=100)
    for i in range(30):
        jb.push(bytes([i]) * 192 * 10)                    # 300 ms pushed, nothing pulled
    assert jb.buffered_ms() <= 100
    assert jb.pull(192)[0] >= 20                          # the oldest data was discarded


def test_jitter_buffer_underrun_returns_silence_and_rebuffers():
    jb = c.JitterBuffer(target_ms=20, max_ms=200)
    jb.push(b"\x05" * 192 * 20)
    assert jb.pull(192 * 20) == b"\x05" * 192 * 20
    assert jb.pull(192 * 5) == b"\x00" * 192 * 5          # underrun
    assert jb.underruns == 1
    jb.push(b"\x06" * 192 * 10)
    assert jb.pull(192 * 5) == b"\x00" * 192 * 5          # re-buffering to the target again
