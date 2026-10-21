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


def posted(m):
    return [b["text"] for p, b in m.posts if p == "/clipboard"]


def test_stop_interrupts_a_pending_long_poll(mock):
    mock.clip_poll = 20.0
    s = c.ClipboardSync(c.BridgeHttp(mock.port, PASSWORD), on_device_text=lambda t: None)
    s.start()
    assert wait(lambda: len(polls(mock)) >= 2)
    t = time.monotonic()
    s.stop()
    assert time.monotonic() - t < 0.5 and not s._thread.is_alive()


def test_newest_host_text_wins_over_an_in_flight_push(mock, sync):
    mock.post_delay = 0.3
    sync.offer_host_text("lama")                   # background push starts
    time.sleep(0.05)
    assert sync.push_host_text("baru") is True     # e.g. Ctrl+V right after a second copy
    time.sleep(0.8)
    assert mock.clip == "baru" and posted(mock)[-1] == "baru"


def test_paste_waits_for_the_in_flight_push_of_the_same_text(mock, sync):
    mock.post_delay = 0.3
    sync.offer_host_text("X")
    time.sleep(0.05)
    sync.push_host_text("X")                       # must not return before X is on the device
    assert mock.clip == "X"
    assert posted(mock) == ["X"]


def test_failed_push_is_retried_when_the_bridge_is_back(mock):
    api = c.BridgeHttp(1, PASSWORD)                # nothing listens on port 1
    s = c.ClipboardSync(api, on_device_text=lambda t: None, retry=0.1)
    s.start()
    try:
        s.offer_host_text("tertunda")
        time.sleep(0.3)
        api.rebind(mock.port, PASSWORD)
        assert wait(lambda: posted(mock) == ["tertunda"])
    finally:
        s.stop()


def test_device_copy_replaces_the_pending_host_text(mock, sync):
    assert wait(lambda: len(polls(mock)) >= 1)
    sync.offer_host_text("A")
    assert wait(lambda: posted(mock) == ["A"])
    mock.device_copy("B")
    assert wait(lambda: sync.got == ["B"])
    sync.offer_host_text("B")                      # the PC clipboard now shows B
    time.sleep(0.5)
    assert posted(mock) == ["A"]                   # neither B (echo) nor A (stale) is sent


def test_malformed_event_body_does_not_kill_the_sync(mock, sync):
    mock.clip_events_body = b"[1, 2]"
    time.sleep(0.5)
    mock.clip_events_body = None
    assert sync._thread.is_alive()
    assert wait(lambda: len(polls(mock)) >= 3)
    mock.device_copy("hidup")
    assert wait(lambda: sync.got == ["hidup"], 6)
