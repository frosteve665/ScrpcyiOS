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


def make_player(hevc_mp4, autosync=False):
    import player as pl
    m = MockBridge(hevc_mp4)
    m.pw = PASSWORD
    m.clip_poll = 0.5
    dev = c.IOSDevice.__new__(c.IOSDevice)
    dev.api = c.BridgeHttp(m.port, PASSWORD)
    dev.video = c.VideoSession(dev.api)
    dev.input = c.InputController(dev.api)
    dev.supervisor = None
    dev.audio = dev.clipboard = None
    dev.bridge = c.BridgeProcess(python="py", bridge_path="x.py")
    dev.video.start()
    clip_in = None
    if autosync:
        import queue
        clip_in = queue.Queue()
        dev.clipboard = c.ClipboardSync(dev.api, clip_in.put)
        dev.clipboard.start()
    try:
        p = pl.Player(dev, "test", clipboard_in=clip_in)
    except tk.TclError as exc:
        m.close()
        pytest.skip(f"Tk cannot open a display: {exc}")
    return p, m, dev


@pytest.fixture
def player(hevc_mp4):
    p, m, dev = make_player(hevc_mp4)
    yield p, m
    p.root.destroy()
    dev.input.close()
    dev.video.stop()
    m.close()


@pytest.fixture
def synced(hevc_mp4):
    p, m, dev = make_player(hevc_mp4, autosync=True)
    yield p, m
    p.root.destroy()
    dev.close()
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


def test_mod_sequence_never_sends_alt_to_the_iphone(player):
    p, m = player
    pump(p, 0.3)
    mask = p.mod_mask

    class Ev:
        def __init__(self, keysym, state):
            self.keysym, self.state = keysym, state
    p._key_down(Ev("Alt_L", 0))            # Tk reports the Alt press itself without the Alt bit
    p._key_down(Ev("v", mask))             # MOD+V = paste
    p._key_up(Ev("v", mask))
    p._key_up(Ev("Alt_L", mask))
    p._key_down(Ev("a", 0))                # a normal key still goes through
    p._key_up(Ev("a", 0))
    deadline = time.time() + 3
    while time.time() < deadline and len([q for q in m.posts if q[0] == "/key"]) < 3:
        time.sleep(0.02)
    keys = [b["usages"] for path, b in m.posts if path == "/key"]
    assert keys, "no /key posts at all"
    assert all(0xE2 not in k and 0xE6 not in k for k in keys)
    assert any(0xE3 in k and 0x19 in k for k in keys)          # Cmd+V for the paste
    assert any(k == [0x04] for k in keys)                        # plain 'a'


def clip_posts(m):
    return [b["text"] for path, b in m.posts if path == "/clipboard"]


def test_device_copy_lands_on_the_pc_clipboard_without_echo(synced):
    p, m = synced
    pump(p, 0.8)
    m.device_copy("dari iPhone")
    end = time.time() + 3
    while time.time() < end and _host(p) != "dari iPhone":
        pump(p, 0.05)
    assert _host(p) == "dari iPhone"
    pump(p, 1.2)                                  # host watcher runs at least twice
    assert clip_posts(m) == []                    # not sent back to the iPhone


def test_pc_copy_is_sent_to_the_iphone_once(synced):
    p, m = synced
    pump(p, 0.3)
    p.root.clipboard_clear()
    p.root.clipboard_append("dari PC")
    end = time.time() + 3
    while time.time() < end and not clip_posts(m):
        pump(p, 0.05)
    pump(p, 1.2)
    assert clip_posts(m) == ["dari PC"]


def _host(p):
    try:
        return p.root.clipboard_get()
    except tk.TclError:
        return None
