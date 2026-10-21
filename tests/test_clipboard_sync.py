"""Automatic two-way clipboard (like scrcpy's default): long-poll /clipboard/events + host pushes."""
import time

import pytest
from mock_bridge import PASSWORD, MockBridge

import ios_client as c


@pytest.fixture
def mock(hevc_mp4):
    m = MockBridge(hevc_mp4)
    m.clip_poll = 0.5
    yield m
    m.close()


def wait(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def polls(m):
    return [h for h in m.hits if h.startswith("/clipboard/events")]


@pytest.fixture
def sync(mock):
    got = []
    s = c.ClipboardSync(c.BridgeHttp(mock.port, PASSWORD), on_device_text=got.append)
    s.got = got
    s.start()
    yield s
    s.stop()


def test_device_copy_reaches_the_host(mock, sync):
    assert wait(lambda: len(polls(mock)) >= 1)
    mock.device_copy("dari iPhone")
    assert wait(lambda: sync.got == ["dari iPhone"])


def test_what_the_device_held_before_sync_is_not_reported(mock, sync):
    assert wait(lambda: len(polls(mock)) >= 2)       # first poll + a timed-out long-poll
    assert sync.got == []


def test_host_push_posts_once_per_distinct_text(mock, sync):
    assert sync.push_host_text("dari PC") is True
    assert sync.push_host_text("dari PC") is False
    assert [b for p, b in mock.posts if p == "/clipboard"] == [{"text": "dari PC"}]


def test_host_push_is_not_echoed_back_to_the_host(mock, sync):
    assert wait(lambda: len(polls(mock)) >= 1)
    sync.push_host_text("ping")
    mock.device_copy("ping")                          # the device reports what we just set
    mock.device_copy("pong")
    assert wait(lambda: sync.got == ["pong"])


def test_device_text_is_not_pushed_back_to_the_device(mock, sync):
    assert wait(lambda: len(polls(mock)) >= 1)
    mock.device_copy("abc")
    assert wait(lambda: sync.got == ["abc"])
    assert sync.push_host_text("abc") is False        # host watcher sees the text we just put there
    assert not [p for p, _ in mock.posts if p == "/clipboard"]


def test_unreachable_bridge_backs_off_and_stops_cleanly():
    s = c.ClipboardSync(c.BridgeHttp(1, PASSWORD), on_device_text=lambda t: None)
    s.start()
    time.sleep(0.3)
    t = time.monotonic()
    s.stop()
    assert time.monotonic() - t < 2.0 and not s._thread.is_alive()
