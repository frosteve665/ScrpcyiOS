import os
import sys
import pytest
import ios_client as c

FAKE = os.path.join(os.path.dirname(__file__), "fake_bridge.py")


def proc(mode, **kw):
    bp = c.BridgeProcess(python=sys.executable, bridge_path=FAKE, extra_args=[mode], **kw)
    return bp


def test_ready_event_yields_info_and_status_callbacks():
    seen = []
    bp = proc("ready", on_status=seen.append)
    try:
        info = bp.start(timeout=10)
        assert (info.port, info.password, info.udid, info.ios) == (1234, "pw", "U1", "17.5")
        assert bp.alive() and seen[0]["phase"] == "detecting"
    finally:
        bp.stop()
    assert not bp.alive()


def test_error_event_raises_with_code():
    bp = proc("error")
    with pytest.raises(c.BridgeError) as e:
        bp.start(timeout=10)
    assert e.value.code == "developer_mode_off" and "mati" in str(e.value)


def test_exit_before_ready_is_reported():
    with pytest.raises(c.BridgeError) as e:
        proc("exit").start(timeout=10)
    assert e.value.code == "bridge_exited"


def test_timeout_when_bridge_never_ready():
    bp = proc("hang")
    with pytest.raises(c.BridgeError) as e:
        bp.start(timeout=1.0)
    assert e.value.code == "timeout" and not bp.alive()


def test_newer_protocol_is_rejected():
    bp = proc("ready_v2")
    with pytest.raises(c.BridgeError) as e:
        bp.start(timeout=10)
    assert e.value.code == "protocol_mismatch" and not bp.alive()


def test_missing_protocol_field_means_v1():
    bp = proc("legacy_ready")
    try:
        assert bp.start(timeout=10).port == 1
    finally:
        bp.stop()
