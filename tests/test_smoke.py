import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import pytest
from mock_bridge import MockBridge, PASSWORD
import ios_client as c
import smoke_test as st


@pytest.fixture
def mock(hevc_mp4):
    m = MockBridge(hevc_mp4)
    yield m
    m.close()


def test_smoke_passes_against_healthy_bridge(mock):
    r = st.run_smoke(c.BridgeHttp(mock.port, PASSWORD), min_frames=20, timeout=10)
    assert r["ok"] is True
    assert r["frames"] >= 20 and r["width"] == 320 and r["height"] == 240
    assert r["first_frame_ms"] is not None and r["touch_sent"] is True


def test_smoke_reports_failure_when_auth_is_wrong(mock):
    r = st.run_smoke(c.BridgeHttp(mock.port, "wrong"), min_frames=5, timeout=1.5)
    assert r["ok"] is False and r["frames"] == 0 and r["error"]
