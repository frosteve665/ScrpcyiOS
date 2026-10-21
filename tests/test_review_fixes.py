"""Regression tests for the Sprint 1 review findings (C1, I1, I2, I3, I4, I5, I6)."""
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

import frame_fix as ff
import ios_client as c
import player_logic as pl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fake_bridge.py")
BRIDGE_DIR = os.path.join(ROOT, "bridge")


def spawn(code):
    return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, cwd=BRIDGE_DIR)


# ---- C1: parent really dies -> both pipes closed -> bridge must still exit ---------------------
def test_real_parent_gone_handler_exits_even_with_stdout_closed():
    p = spawn("import sys, time, ios_bridge as b\n"
              "b.watch_stdin(sys.stdin, lambda: b.parent_gone(None, None))\n"
              "time.sleep(30)\n")
    time.sleep(0.5)
    p.stdout.close()                      # no reader any more: emit() would raise BrokenPipe
    p.stdin.close()
    assert p.wait(5) == 0


def test_emit_survives_a_closed_stdout():
    p = spawn("import sys, time, ios_bridge as b\n"
              "time.sleep(0.5)\n"
              "for _ in range(50): b.emit('status', phase='x')\n"
              "sys.exit(7)\n")
    p.stdout.close()
    assert p.wait(5) == 7                 # reached the end, did not die on BrokenPipeError


# ---- I6: graceful path cancels the asyncio task (cleanup runs), os._exit only as fallback -------
def test_parent_gone_cancels_the_running_task_so_finally_blocks_run(tmp_path):
    marker = tmp_path / "cleaned"
    p = spawn("import asyncio, sys, ios_bridge as b\n"
              "async def main():\n"
              "    b.install_parent_watch(sys.stdin, grace=10)\n"
              "    try:\n"
              "        await asyncio.sleep(30)\n"
              "    finally:\n"
              f"        open(r'{marker}', 'w').write('ok')\n"
              "try:\n"
              "    asyncio.run(main())\n"
              "except asyncio.CancelledError:\n"
              "    sys.exit(0)\n")
    time.sleep(0.5)
    p.stdout.close()
    p.stdin.close()
    t0 = time.monotonic()
    assert p.wait(5) == 0
    assert time.monotonic() - t0 < 5 and marker.read_text() == "ok"


# ---- I1: stop()/close() racing start() must not leak a live bridge ---------------------------------
def proc(mode):
    return c.BridgeProcess(python=sys.executable, bridge_path=FAKE, extra_args=[mode])


def test_close_during_start_kills_the_bridge_and_start_fails():
    bp = proc("slow_ready")
    err = []

    def go():
        try:
            bp.start(timeout=10)
        except c.BridgeError as e:
            err.append(e.code)
    t = threading.Thread(target=go)
    t.start()
    time.sleep(0.4)
    bp.close()
    t.join(10)
    assert err == ["closed"]
    time.sleep(0.2)
    assert not bp.alive()


def test_start_after_close_is_refused():
    bp = proc("ready_eof")
    bp.close()
    with pytest.raises(c.BridgeError) as e:
        bp.start(timeout=5)
    assert e.value.code == "closed" and bp.proc is None


def test_supervisor_rebinds_to_the_new_port_after_respawn():
    api = c.BridgeHttp(1, "a")
    bp = proc("ready_then_die")
    sup = c.BridgeSupervisor(bp, api, policy=c.ReconnectPolicy(base=0.05, cap=0.1), poll=0.05)
    first = sup.start_initial(timeout=10)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and sup.restarts < 1:
            time.sleep(0.05)
        assert sup.restarts >= 1 and api.port == bp.info.port and api.port != first.port
    finally:
        sup.stop()
    assert not bp.alive()


# ---- I5: fatal codes stop retrying; a recording is stopped when the bridge drops ------------------
class StubBridge:
    def __init__(self, code):
        self.code, self.calls, self.closed = code, 0, False

    def start(self, timeout=120.0):
        self.calls += 1
        if self.calls == 1:
            return c.BridgeInfo(5, "p", "U", "17.5")
        raise c.BridgeError(self.code, "stub")

    def alive(self):
        return False

    def stop(self):
        pass

    def close(self):
        self.closed = True


@pytest.mark.parametrize("code", ["unsupported_ios", "protocol_mismatch", "port_in_use"])
def test_supervisor_gives_up_on_fatal_codes(code):
    events = []
    br = StubBridge(code)
    sup = c.BridgeSupervisor(br, c.BridgeHttp(1, "a"), policy=c.ReconnectPolicy(base=0.01, cap=0.01),
                             on_event=events.append, poll=0.02)
    sup.start_initial()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not any(e["event"] == "gave_up" for e in events):
        time.sleep(0.02)
    sup.stop()
    assert br.calls == 2 and events[-1] == {"event": "gave_up", "code": code}


def test_transient_codes_keep_retrying():
    events = []
    br = StubBridge("device_not_found")      # cable pulled: keep trying
    sup = c.BridgeSupervisor(br, c.BridgeHttp(1, "a"), policy=c.ReconnectPolicy(base=0.01, cap=0.01),
                             on_event=events.append, poll=0.02)
    sup.start_initial()
    time.sleep(0.5)
    sup.stop()
    assert br.calls > 5 and not any(e["event"] == "gave_up" for e in events)


def test_device_stops_recording_when_bridge_goes_down():
    dev = c.IOSDevice.__new__(c.IOSDevice)
    dev.on_event = None

    class V:
        stopped = False

        def stop_recording(self):
            V.stopped = True
    dev.video = V()
    dev._on_supervisor_event({"event": "bridge_down"})
    assert V.stopped and dev.link_state == "reconnecting"
    dev._on_supervisor_event({"event": "restarted", "port": 9})
    assert dev.link_state == "ok"


# ---- I2 / I3: Alt is MOD, never typed; AltGr (Ctrl+Alt) is not MOD -----------------------------------
def test_alt_keys_are_never_forwarded():
    ks = pl.KeyState()
    assert ks.press("Alt_L") is None and ks.press("Alt_R") is None
    assert pl.is_mod_key("Alt_L") and pl.is_mod_key("Alt_R") and not pl.is_mod_key("Shift_L")


def test_altgr_is_not_mod():
    ctrl = 0x4
    assert not pl.mod_pressed(pl.MOD_MASK_WINDOWS | ctrl, pl.MOD_MASK_WINDOWS)


# ---- I4: a static mid-gray screen must not be cropped -------------------------------------------------
H, W = 344, 197


def gray_ui():
    """A real UI: content top-left, a flat 128-gray panel elsewhere, but with text/edges in it."""
    a = np.full((H, W, 3), 128, dtype=np.uint8)
    a[: H // 2, : W // 2] = np.random.default_rng(3).integers(0, 256, (H // 2, W // 2, 3), dtype=np.uint8)
    a[H - 40: H - 30, 20: W - 20] = 20        # a dark text line in the "padding" area
    return a


def collapsed():
    a = np.full((H, W, 3), 128, dtype=np.uint8)
    a[: H // 2, : W // 2] = np.random.default_rng(4).integers(0, 256, (H // 2, W // 2, 3), dtype=np.uint8)
    return a


def test_padding_with_any_content_is_not_a_collapse():
    assert ff.detect_content_crop(gray_ui()) is None


def test_stabilizer_releases_a_crop_that_lasts_too_long():
    st = ff.CropStabilizer(max_hold=1.0)
    frame = collapsed()
    assert st.update(frame, now=0.0) is not None          # collapse is transient: engage
    assert st.update(frame, now=0.5) is not None
    assert st.update(frame, now=1.2) is None              # a static screen is not a collapse
    assert st.update(frame, now=5.0) is None              # stays released while unchanged
    full = np.random.default_rng(5).integers(0, 256, (H, W, 3), dtype=np.uint8)
    assert st.update(full, now=6.0) is None
    assert st.update(frame, now=6.1) is not None          # a fresh collapse engages again


def test_stabilizer_needs_a_full_frame_before_engaging():
    st = ff.CropStabilizer(max_hold=1.0)
    assert st.update(collapsed(), now=0.0, first=True) is None   # app opened straight onto a gray layout
