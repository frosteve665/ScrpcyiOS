"""scrcpy-style command line options of the player (parsed without Tk)."""
import player_logic as p


def test_defaults_match_scrcpy():
    a = p.parse_args([])
    assert a.audio is True and a.audio_buffer == 50 and a.clipboard_autosync is True
    assert a.record is None and a.fullscreen is False and a.window_title is None
    assert "--no-audio" not in p.bridge_args(a)


def test_no_audio_is_passed_to_the_bridge():
    a = p.parse_args(["--no-audio"])
    assert a.audio is False and "--no-audio" in p.bridge_args(a)


def test_audio_buffer_and_clipboard_and_window_options():
    a = p.parse_args(["--audio-buffer", "120", "--no-clipboard-autosync", "-f", "--window-title", "HP",
                      "--record", "x.mp4", "--udid", "abc"])
    assert a.audio_buffer == 120 and a.clipboard_autosync is False and a.fullscreen is True
    assert a.window_title == "HP" and a.record == "x.mp4" and a.udid == "abc"


def test_audio_buffer_rejects_nonsense():
    import pytest
    for bad in ("0", "-5", "5000"):
        with pytest.raises(SystemExit):
            p.parse_args(["--audio-buffer", bad])
