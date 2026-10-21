"""Guards against pymobiledevice3 API drift: every symbol the bridge touches must still exist."""
import importlib
import inspect
import pytest

pytest.importorskip("pymobiledevice3")

# (module, attribute, required parameter names or None)
CONTRACT = [
    ("pymobiledevice3.usbmux", "list_devices", None),
    ("pymobiledevice3.lockdown", "create_using_usbmux", ["serial", "autopair"]),
    ("pymobiledevice3.remote.rsd_tunnel", "PreferredRsdTunnel", ["serial", "autopair"]),
    ("pymobiledevice3.services.mobile_image_mounter", "auto_mount", None),
    ("pymobiledevice3.remote.core_device.screen_stream", "ScreenStreamServer",
     ["bind", "http_port", "password", "audio_default_on", "max_bitrate_kbps", "motion_idr"]),
    ("pymobiledevice3.remote.core_device.display_service", "is_media_in_use_error", None),
    ("pymobiledevice3.remote.core_device.hid_service", "touch_session", None),
    ("pymobiledevice3.remote.core_device.hid_service", "build_touchscreen_report", ["state", "x", "y", "timestamp"]),
]


def missing(contract):
    problems = []
    for module, attr, params in contract:
        try:
            obj = getattr(importlib.import_module(module), attr)
        except (ImportError, AttributeError) as exc:
            problems.append(f"{module}.{attr}: {exc}")
            continue
        if params:
            target = obj.__init__ if inspect.isclass(obj) else obj
            have = set(inspect.signature(target).parameters)
            lost = [p for p in params if p not in have]
            if lost:
                problems.append(f"{module}.{attr} lost parameters {lost}")
    return problems


def test_checker_detects_drift():
    assert missing([("pymobiledevice3.usbmux", "does_not_exist", None)])
    assert missing([("pymobiledevice3.remote.rsd_tunnel", "PreferredRsdTunnel", ["no_such_param"])])


def test_bridge_dependencies_still_exist():
    assert missing(CONTRACT) == []


def test_screen_stream_audio_hooks_exist_for_portable_audio():
    """bridge/audio_portable.py patches these two names; fail loudly if pymobiledevice3 renames them."""
    import pymobiledevice3.remote.core_device.screen_stream as ss
    assert hasattr(ss, "AACELDDecoder") and hasattr(ss, "AAC_ELD_ASC_48K_STEREO_480")
    assert callable(getattr(ss.ScreenStreamServer, "_missing_audio_deps", None))
