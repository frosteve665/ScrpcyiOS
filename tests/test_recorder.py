import av
from mock_bridge import MockBridge, PASSWORD, load_hevc
import ios_client as c
import recorder as r


def count_frames(path):
    with av.open(path) as f:
        stream = f.streams.video[0]
        n = sum(1 for _ in f.decode(video=0))
        return stream.codec_context.name, stream.width, stream.height, n


def test_recorder_remuxes_without_reencoding(hevc_mp4, tmp_path):
    hvcc, aus = load_hevc(hevc_mp4)
    out = tmp_path / "out.mp4"
    rec = r.Mp4Recorder(str(out), hvcc, 320, 240)
    t = 0.0
    for is_key, au in aus:
        rec.write(c.FRAME_KEY if is_key else c.FRAME_DELTA, au, t)
        t += 1 / 30
    rec.close()
    codec, w, h, n = count_frames(str(out))
    assert (codec, w, h) == ("hevc", 320, 240)
    assert n == len(aus)


def test_recorder_skips_deltas_until_first_keyframe(hevc_mp4, tmp_path):
    hvcc, aus = load_hevc(hevc_mp4)
    first_key = next(i for i, (k, _) in enumerate(aus) if k)
    deltas = [a for k, a in aus if not k][:3]
    out = tmp_path / "o.mp4"
    rec = r.Mp4Recorder(str(out), hvcc, 320, 240)
    for i, d in enumerate(deltas):
        rec.write(c.FRAME_DELTA, d, i / 30)         # before any key: must be dropped
    for i in range(first_key, first_key + 10):
        rec.write(c.FRAME_KEY if aus[i][0] else c.FRAME_DELTA, aus[i][1], 1 + i / 30)
    rec.close()
    assert count_frames(str(out))[3] == 10


def test_recorder_timestamps_stay_strictly_increasing_even_if_clock_repeats(hevc_mp4, tmp_path):
    hvcc, aus = load_hevc(hevc_mp4)
    out = tmp_path / "o.mp4"
    rec = r.Mp4Recorder(str(out), hvcc, 320, 240)
    for k, au in aus[:20]:
        rec.write(c.FRAME_KEY if k else c.FRAME_DELTA, au, 5.0)   # same timestamp every time
    rec.close()
    assert count_frames(str(out))[3] == 20


def test_session_records_live_stream_to_playable_mp4(hevc_mp4, tmp_path):
    import time
    m = MockBridge(hevc_mp4)
    try:
        v = c.VideoSession(c.BridgeHttp(m.port, PASSWORD))
        out = tmp_path / "live.mp4"
        v.start_recording(str(out))
        v.start()
        end = time.time() + 5
        while v.frames < 40 and time.time() < end:
            time.sleep(0.05)
        v.stop_recording()
        v.stop()
    finally:
        m.close()
    codec, w, h, n = count_frames(str(out))
    assert codec == "hevc" and (w, h) == (320, 240) and n >= 30


def test_stop_recording_without_start_is_safe():
    c.VideoSession(c.BridgeHttp(1, "x")).stop_recording()



def test_creating_recorders_is_reliable_not_flaky(hevc_mp4, tmp_path):
    """Regression: the 1-frame template encode used to fail ~10-20% of the time (B-frames + threads)."""
    hvcc, _ = load_hevc(hevc_mp4)
    for i in range(60):
        r.Mp4Recorder(str(tmp_path / f"r{i}.mp4"), hvcc, 320, 240).close()
