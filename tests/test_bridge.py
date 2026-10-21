import pytest
import ios_bridge as b


@pytest.mark.parametrize("v,ok", [("17.4", True), ("17.4.1", True), ("18.0", True), ("17.3.1", False), ("16.7", False)])
def test_ios_version_gate(v, ok):
    if ok:
        b.check_ios_version(v)
    else:
        with pytest.raises(b.BridgeError) as e:
            b.check_ios_version(v)
        assert e.value.code == "unsupported_ios"


def test_parse_version_tolerates_junk():
    assert b.parse_version("17.5 (beta)") == (17, 5, 0)


def test_classify_known_errors():
    class DeviceNotFoundError(Exception): ...
    class ConnectionFailedToUsbmuxdError(Exception): ...
    assert b.classify(DeviceNotFoundError()).code == "device_not_found"
    assert b.classify(ConnectionFailedToUsbmuxdError("x")).code == "driver_missing"
    assert b.classify(RuntimeError("PairingDialogResponsePending")).code == "not_trusted"
    assert b.classify(RuntimeError("boom")).code == "unknown"


def test_free_port():
    assert 0 < b.free_port() < 65536
