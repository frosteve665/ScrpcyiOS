import io, time, pytest
from mock_bridge import MockBridge, PASSWORD, frame_msg
import ios_client as c

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


def test_frame_parser_handles_split_reads():
    data = frame_msg(0, b"AAAA") + frame_msg(1, b"BB")
    stream = io.BytesIO(data)
    out = list(c.iter_frames(lambda n: stream.read(min(n, 3))))  # tiny reads
    assert out == [(0, b"AAAA"), (1, b"BB")]


def test_frame_parser_rejects_absurd_length():
    with pytest.raises(c.BridgeError):
        list(c.iter_frames(io.BytesIO(b"\xff\xff\xff\xff\x00").read))


def test_truncated_stream_ends_cleanly():
    assert list(c.iter_frames(io.BytesIO(frame_msg(0, b"AAAA")[:-2]).read)) == []


def test_auth_required(mock):
    bad = c.BridgeHttp(mock.port, "wrong")
    assert bad.request("GET", "/clipboard")[0] == 401


def test_codec_retries_on_503(mock):
    mock.codec_503_first = 2
    api = c.BridgeHttp(mock.port, PASSWORD)
    codec, hvcc = api.codec(delay=0.01)
    assert codec.startswith("hev1") and hvcc == mock.hvcc


def test_real_hevc_decodes_end_to_end(mock):
    api = c.BridgeHttp(mock.port, PASSWORD)
    v = c.VideoSession(api)
    v.start()
    assert wait(lambda: v.frames >= 20), f"only {v.frames} frames, err={v.last_error}"
    v.stop()
    assert (v.latest.width, v.latest.height) == (320, 240)


def test_screenshot_pillow(mock, tmp_path):
    api = c.BridgeHttp(mock.port, PASSWORD)
    dev = c.IOSDevice.__new__(c.IOSDevice)
    dev.api, dev.video = api, c.VideoSession(api)
    dev.video.start()
    assert wait(lambda: dev.video.frames > 0)
    out = tmp_path / "s.png"
    assert dev.screenshot(str(out)) and out.stat().st_size > 100
    dev.video.stop()


def test_touch_single_finger_policy(mock):
    api = c.BridgeHttp(mock.port, PASSWORD)
    inp = c.InputController(api)
    assert inp.down(1, 0.5, 0.5) is True
    assert inp.down(2, 0.1, 0.1) is False      # second finger ignored
    inp.move(2, 0.9, 0.9)                         # ignored
    inp.up(2, 0.9, 0.9)                           # ignored
    inp.up(1, 0.5, 0.5)
    assert wait(lambda: len(mock.posts) >= 2)
    kinds = [b["type"] for p, b in mock.posts if p == "/touch"]
    assert kinds == ["contact", "release"]
    assert mock.posts[0][1]["x"] == round(0.5 * 65535)
    inp.close()


def test_multitouch_flag_allows_second_pointer(mock):
    inp = c.InputController(c.BridgeHttp(mock.port, PASSWORD), multitouch=True)
    assert inp.down(1, 0.2, 0.2) and inp.down(2, 0.8, 0.8)
    inp.close()


def test_move_coalescing_sends_latest(mock):
    api = c.BridgeHttp(mock.port, PASSWORD)
    inp = c.InputController(api)
    inp.down(0, 0, 0)
    for i in range(1, 200):
        inp.move(0, i / 200, i / 200)
    inp.up(0, 1, 1)
    assert wait(lambda: any(b["type"] == "release" for _, b in mock.posts))
    moves = [b for p, b in mock.posts if p == "/touch" and b["type"] == "contact"]
    assert len(moves) < 200            # coalesced
    assert mock.posts[-1][1]["type"] == "release"
    inp.close()


def test_buttons_and_keys_and_validation(mock):
    inp = c.InputController(c.BridgeHttp(mock.port, PASSWORD))
    inp.button("home"); inp.keys([0x04, 0xe3])
    with pytest.raises(ValueError):
        inp.button("nope")
    assert wait(lambda: len(mock.posts) >= 2)
    assert ("/button", {"name": "home", "state": "press"}) in mock.posts
    assert ("/key", {"usages": [4, 227]}) in mock.posts
    inp.close()


def test_clipboard_and_rotate(mock):
    dev = c.IOSDevice.__new__(c.IOSDevice)
    dev.api = c.BridgeHttp(mock.port, PASSWORD)
    assert dev.clipboard_get()["text"] == "hello"
    assert dev.clipboard_set("halo") and mock.clip == "halo"
    assert dev.rotate("left")["orientation"] == "landscape"
