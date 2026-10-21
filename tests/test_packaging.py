import sys

import ios_client as c


def test_source_run_uses_python_and_bridge_script(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("STRIKEMIRROR_BRIDGE", raising=False)
    cmd = c.default_bridge_cmd()
    assert cmd[0] == sys.executable and cmd[1].endswith("ios_bridge.py")


def test_frozen_player_finds_sibling_bridge_exe(monkeypatch, tmp_path):
    exe_name = "strikemirror-bridge.exe" if sys.platform == "win32" else "strikemirror-bridge"
    player_dir, bridge_dir = tmp_path / "strikemirror-player", tmp_path / "strikemirror-bridge"
    player_dir.mkdir()
    bridge_dir.mkdir()
    (bridge_dir / exe_name).write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(player_dir / "strikemirror-player.exe"))
    monkeypatch.delenv("STRIKEMIRROR_BRIDGE", raising=False)
    assert c.default_bridge_cmd() == [str(bridge_dir / exe_name)]


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("STRIKEMIRROR_BRIDGE", "C:/x/bridge.exe")
    assert c.default_bridge_cmd() == ["C:/x/bridge.exe"]


def test_bridge_process_uses_default_cmd(monkeypatch):
    monkeypatch.setenv("STRIKEMIRROR_BRIDGE", "/opt/bridge")
    bp = c.BridgeProcess(udid="U")
    assert bp.cmd[:3] == ["/opt/bridge", "--udid", "U"]


def test_client_does_not_import_pymobiledevice3():
    import ast
    import pathlib
    for f in pathlib.Path(c.__file__).parent.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(x.startswith("pymobiledevice3") for x in names), f"{f.name} imports pymobiledevice3"
