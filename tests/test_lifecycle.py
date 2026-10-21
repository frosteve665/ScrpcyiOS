import os
import sys
import threading
import time

import pytest

import ios_bridge as b
import ios_client as c

FAKE = os.path.join(os.path.dirname(__file__), "fake_bridge.py")


def proc(mode, **kw):
    return c.BridgeProcess(python=sys.executable, bridge_path=FAKE, extra_args=[mode], **kw)


# ---- bridge side: die with the client -------------------------------------------------
def test_watch_stdin_fires_on_eof():
    r, w = os.pipe()
    fired = threading.Event()
    with os.fdopen(r) as stream:
        b.watch_stdin(stream, fired.set)
        assert not fired.wait(0.2)          # still open, so no exit
        os.close(w)
        assert fired.wait(2.0)


def test_watch_stdin_ignores_data_until_eof():
    r, w = os.pipe()
    fired = threading.Event()
    with os.fdopen(r) as stream:
        b.watch_stdin(stream, fired.set)
        os.write(w, b"noise\n")
        assert not fired.wait(0.2)
        os.close(w)
        assert fired.wait(2.0)


def test_port_in_use_is_classified():
    import errno
    err = OSError(errno.EADDRINUSE, "Address already in use")
    assert b.classify(err).code == "port_in_use"


# ---- client side: stdin handshake ----------------------------------------------------
def test_stop_closes_stdin_so_bridge_exits_gracefully():
    bp = proc("ready_eof")
    bp.start(timeout=10)
    t0 = time.monotonic()
    bp.stop()
    assert time.monotonic() - t0 < 3.0
    assert bp.proc.returncode == 0          # graceful exit, not terminate/kill


def test_client_death_ends_bridge():
    bp = proc("ready_eof")
    bp.start(timeout=10)
    bp.proc.stdin.close()                    # what the OS does when the client process dies
    assert bp.proc.wait(5) == 0


def test_bridge_gets_exit_flag():
    bp = c.BridgeProcess(python="py", bridge_path="x.py")
    assert "--exit-on-stdin-eof" in bp.cmd


# ---- supervisor: crash, respawn, rebind ------------------------------------------------
def test_http_rebind_changes_target():
    api = c.BridgeHttp(1, "a")
    api.rebind(2, "b")
    assert (api.port, api.auth) == (2, c._basic("b"))


def test_supervisor_respawns_dead_bridge_and_reports():
    api = c.BridgeHttp(1, "a")
    events = []
    sup = c.BridgeSupervisor(proc("ready_then_die"), api, policy=c.ReconnectPolicy(base=0.05, cap=0.1),
                             on_event=events.append, poll=0.05)
    sup.start_initial(timeout=10)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and sup.restarts < 2:
            time.sleep(0.05)
        assert sup.restarts >= 2
        assert "restarted" in [e["event"] for e in events]
    finally:
        sup.stop()


def test_supervisor_gives_up_after_max_attempts():
    api = c.BridgeHttp(1, "a")
    events = []
    bp = proc("ready_then_die")
    sup = c.BridgeSupervisor(bp, api, policy=c.ReconnectPolicy(base=0.01, cap=0.01, max_attempts=0),
                             on_event=events.append, poll=0.05)
    sup.start_initial(timeout=10)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(e["event"] == "gave_up" for e in events):
            time.sleep(0.05)
        assert any(e["event"] == "gave_up" for e in events)
    finally:
        sup.stop()


def test_supervisor_stop_stops_bridge():
    api = c.BridgeHttp(1, "a")
    bp = proc("ready_eof")
    sup = c.BridgeSupervisor(bp, api, on_event=lambda e: None, poll=0.05)
    sup.start_initial(timeout=10)
    sup.stop()
    assert not bp.alive()


@pytest.mark.parametrize("mode", ["error"])
def test_initial_start_error_propagates(mode):
    sup = c.BridgeSupervisor(proc(mode), c.BridgeHttp(1, "a"), on_event=lambda e: None)
    with pytest.raises(c.BridgeError):
        sup.start_initial(timeout=10)
