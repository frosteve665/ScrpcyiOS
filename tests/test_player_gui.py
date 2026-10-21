"""Real Tk window against the mock bridge. Skipped when there is no tkinter or no display."""
import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("no display (run under xvfb-run)", allow_module_level=True)

from mock_bridge import PASSWORD, MockBridge  # noqa: E402

import ios_client as c  # noqa: E402


@pytest.fixture
def player(hevc_mp4):
    import player as pl
    m = MockBridge(hevc_mp4)
    m.pw = PASSWORD
    dev = c.IOSDevice.__new__(c.IOSDevice)
    dev.api = c.BridgeHttp(m.port, PASSWORD)
    dev.video = c.VideoSession(dev.api)
    dev.input = c.InputController(dev.api)
    dev.supervisor = None
    dev.bridge = c.BridgeProcess(python="py", bridge_path="x.py")
    dev.video.start()
    try:
        p = pl.Player(dev, "test")
    except tk.TclError as exc:
        m.close()
        pytest.skip(f"Tk cannot open a display: {exc}")
    yield p, m
    p.root.destroy()
    dev.input.close()
    dev.video.stop()
    m.close()


def pump(p, seconds=1.5):
    end = time.time() + seconds
    while time.time() < end:
        p.root.update()
        time.sleep(0.01)


def test_player_draws_frames_and_maps_clicks(player):
    p, m = player
    p.root.update()
    pump(p)
    assert p.last_frames > 0 and p.photo is not None
    assert p.frame_size[0] > 0
    x, y, w, h = p.rect

    class E:  # synthetic click in the centre of the picture
        pass
    e = E()
    e.x, e.y = x + w // 2, y + h // 2
    p._touch("down", e)
    p._touch("up", e)
    deadline = time.time() + 3
    while time.time() < deadline and len([q for q in m.posts if q[0] == "/touch"]) < 2:
        time.sleep(0.02)
    touches = [b for path, b in m.posts if path == "/touch"]
    assert touches[0]["type"] == "contact" and touches[-1]["type"] == "release"
    assert abs(touches[0]["x"] - 32767) < 2000 and abs(touches[0]["y"] - 32767) < 2000


def test_mod_shortcut_and_fullscreen_toggle_do_not_crash(player):
    p, m = player
    pump(p, 0.5)

    class E:
        state = p.mod_mask
        keysym = "i"
    p._key_down(E())                     # MOD+I toggles fps display
    assert p.show_fps is False

    class F:
        state = 0
        keysym = "F11"
    p._key_down(F())
    p.root.update()
    p._key_down(F())
    p._action("window-1to1")
    p._action("window-fit")
    p.root.update()
