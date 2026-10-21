"""Client audio playback: GET /audio.bin -> iter_audio -> JitterBuffer -> output (injected in tests)."""
import time

import pytest
from mock_bridge import PASSWORD, MockBridge

import ios_client as c

MS = c.JitterBuffer.BYTES_PER_MS


class FakeOutput:
    """Stands in for the sound card: the test pulls PCM the way the audio callback would."""

    def __init__(self, pull):
        self.pull = pull
        self.closed = False

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, fail=None):
        self.fail, self.outputs = fail, []

    def __call__(self, pull):
        if self.fail:
            raise self.fail
        out = FakeOutput(pull)
        self.outputs.append(out)
        return out


@pytest.fixture
def mock(hevc_mp4):
    m = MockBridge(hevc_mp4)
    yield m
    m.close()


def wait(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def audio_hits(m):
    return [h for h in m.hits if h == "/audio.bin"]


def test_audio_session_plays_pcm_in_order_after_prebuffer(mock):
    mock.audio_msgs = [bytes([i]) * 10 * MS for i in range(1, 7)]   # 6 x 10 ms
    mock.audio_hold = 3.0
    fac = Factory()
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), buffer_ms=30, output_factory=fac)
    s.start()
    try:
        assert wait(lambda: s.jitter.buffered_ms() >= 60)
        assert s.state == "playing" and len(fac.outputs) == 1
        out = fac.outputs[0].pull(60 * MS)
        assert out == b"".join(mock.audio_msgs)
    finally:
        s.stop()


def test_audio_503_means_unavailable_and_no_retry(mock):
    mock.audio_status = 503
    fac = Factory()
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), output_factory=fac)
    s.start()
    try:
        assert wait(lambda: s.state == "unavailable")
        time.sleep(0.6)
        assert len(audio_hits(mock)) == 1 and fac.outputs == []
    finally:
        s.stop()


def test_audio_reconnects_when_the_stream_ends(mock):
    mock.audio_msgs = [b"\x01" * MS]
    fac = Factory()
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), output_factory=fac)
    s.start()
    try:
        assert wait(lambda: len(audio_hits(mock)) >= 2)
        assert len(fac.outputs) == 1          # the sound card stays open across reconnects
    finally:
        s.stop()


def test_no_output_device_disables_audio_quietly(mock):
    mock.audio_msgs = [b"\x01" * MS]
    mock.audio_hold = 2.0
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), output_factory=Factory(fail=OSError("PortAudio library not found")))
    s.start()
    try:
        assert wait(lambda: s.state == "no_output")
        assert "PortAudio" in (s.last_error or "")
        time.sleep(0.6)
        assert len(audio_hits(mock)) == 1
    finally:
        s.stop()


def test_stop_unblocks_a_waiting_reader_and_closes_output(mock):
    mock.audio_msgs = [b"\x01" * MS]
    mock.audio_hold = 10.0
    fac = Factory()
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), output_factory=fac)
    s.start()
    assert wait(lambda: s.state == "playing")
    t = time.monotonic()
    s.stop()
    assert time.monotonic() - t < 0.5                 # the reader is unblocked, not waited out
    assert not s._thread.is_alive() and fac.outputs[0].closed


def test_buffer_option_sets_jitter_target_and_cap():
    s = c.AudioSession(c.BridgeHttp(1, "x"), buffer_ms=80, output_factory=Factory())
    assert s.jitter.target == 80 * MS and s.jitter.max > s.jitter.target


def test_device_runs_audio_and_clipboard_sync_and_stops_them(mock):
    mock.audio_msgs = [b"\x01" * MS]
    mock.audio_hold = 5.0
    mock.clip_poll = 0.5
    got, fac = [], Factory()
    dev = c.IOSDevice(audio=True, audio_buffer_ms=40, on_device_clipboard=got.append, audio_output=fac)
    dev.api = c.BridgeHttp(mock.port, PASSWORD)
    dev._start_media()
    assert dev.audio.jitter.target == 40 * MS
    assert wait(lambda: dev.audio.state == "playing")
    assert wait(lambda: any(h.startswith("/clipboard/events") for h in mock.hits))
    mock.device_copy("salin")
    assert wait(lambda: got == ["salin"])
    assert dev.clipboard_push("salin") is False      # deduplicated through the sync
    assert dev.clipboard_push("baru") is True and mock.clip == "baru"
    dev.close()
    assert dev.audio.state == "stopped" and fac.outputs[0].closed
    assert not dev.clipboard._thread.is_alive()


def test_device_without_audio_or_sync_starts_neither(mock):
    dev = c.IOSDevice()
    dev.api = c.BridgeHttp(mock.port, PASSWORD)
    dev._start_media()
    assert dev.audio is None and dev.clipboard is None
    assert dev.clipboard_push("x") is True and mock.clip == "x"   # plain set without sync
    dev.close()


def test_stop_while_opening_the_output_closes_it(mock):
    mock.audio_msgs = [b"\x01" * MS]
    mock.audio_hold = 3.0
    fac = Factory()

    def slow(pull):
        time.sleep(0.4)
        return fac(pull)
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), output_factory=slow)
    s.start()
    assert wait(lambda: len(audio_hits(mock)) == 1)
    time.sleep(0.1)
    s.stop()
    assert wait(lambda: fac.outputs and fac.outputs[0].closed)
    assert s.state == "stopped"


def test_unexpected_error_restarts_the_stream_instead_of_killing_audio(mock):
    mock.audio_msgs = [b"\x01" * MS] * 3
    fac = Factory()
    s = c.AudioSession(c.BridgeHttp(mock.port, PASSWORD), output_factory=fac)
    real, calls = s.jitter.push, []

    def flaky(pcm):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        real(pcm)
    s.jitter.push = flaky
    s.start()
    try:
        assert wait(lambda: len(audio_hits(mock)) >= 2 and s._thread.is_alive())
        assert "boom" in (s.last_error or "")
    finally:
        s.stop()


def test_device_close_is_fast_with_silent_audio_and_a_pending_long_poll(mock):
    mock.audio_msgs = []
    mock.audio_hold = 20.0
    mock.clip_poll = 20.0
    dev = c.IOSDevice(audio=True, on_device_clipboard=lambda t: None, audio_output=Factory())
    dev.api = c.BridgeHttp(mock.port, PASSWORD)
    dev._start_media()
    assert wait(lambda: dev.audio.state == "playing")
    assert wait(lambda: len([h for h in mock.hits if h.startswith("/clipboard/events")]) >= 2)
    t = time.monotonic()
    dev.close()
    assert time.monotonic() - t < 0.5
    assert not dev.audio._thread.is_alive() and not dev.clipboard._thread.is_alive()
