"""StrikeMirror iOS bridge.

LICENSE NOTE: this file imports pymobiledevice3 (GPL-3.0), so it is GPL-3.0 too.
It is meant to run as its OWN PROCESS. StrikeMirror talks to it over local HTTP
only and never imports it, which keeps the GPL boundary at the process edge.

Protocol (stdout, one JSON object per line):
  {"event":"status","phase":"...","note":"..."}
  {"event":"ready","port":N,"password":"...","udid":"...","ios":"17.5"}
  {"event":"error","code":"...","message":"..."}
After "ready", the stream server (HTTP Basic, any user name) serves on 127.0.0.1:N:
  GET /codec  GET /stream.bin  GET /audio.bin  POST /touch /button /key /rotate /restart /pli
  GET|POST /clipboard
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import secrets
import socket
import sys
import threading

DISPLAY_SERVICE = "com.apple.coredevice.displayservice"
PROTOCOL_VERSION = 1

MESSAGES = {
    "driver_missing": "Apple Mobile Device Service tidak jalan. Pasang 'Apple Devices' dari Microsoft Store, lalu colok ulang iPhone.",
    "device_not_found": "iPhone tidak terdeteksi. Colok kabel, buka kunci layar, lalu coba lagi.",
    "not_trusted": "Buka kunci iPhone dan tekan 'Percayai' (Trust) di HP.",
    "developer_mode_off": "Developer Mode mati. Di iPhone: Pengaturan > Privasi & Keamanan > Developer Mode > nyalakan, HP akan restart, lalu konfirmasi 'Turn On'.",
    "unsupported_ios": "Butuh iOS 17.4 atau lebih baru.",
    "multiple_devices": "Ada lebih dari satu iPhone. Pilih salah satu dengan --udid.",
    "display_service_missing": "iPhone belum menawarkan layanan layar. Cabut dan colok lagi, lalu coba lagi.",
}


def ready_event(port: int, password: str, udid: str, ios: str) -> dict:
    return {"port": port, "password": password, "udid": udid, "ios": ios, "protocol": PROTOCOL_VERSION}


def emit(event: str, **fields) -> None:
    """One JSON event per line on stdout. Never raises: the reader may already be gone."""
    try:
        print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)
    except (OSError, ValueError):
        pass


class BridgeError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or MESSAGES.get(code, code))
        self.code = code


def classify(exc: BaseException) -> BridgeError:
    """Map low-level exceptions to a stable error code (so the UI can guide the user)."""
    if isinstance(exc, BridgeError):
        return exc
    name, text = type(exc).__name__, str(exc)
    low = text.lower()
    if isinstance(exc, OSError) and exc.errno in (errno.EADDRINUSE, 10048):  # 10048 = WSAEADDRINUSE
        return BridgeError("port_in_use", "Port bridge sedang dipakai proses lain.")
    if name in {"ConnectionFailedToUsbmuxdError", "MuxException"} or "27015" in text or "usbmux" in low:
        return BridgeError("driver_missing")
    if name == "DeviceNotFoundError":
        return BridgeError("device_not_found")
    if name in {"DeveloperModeIsNotEnabledError", "DeveloperModeError"}:
        return BridgeError("developer_mode_off")
    if "PairingDialog" in text or "UserDeniedPairing" in text or "InvalidHostID" in text or name == "NotPairedError":
        return BridgeError("not_trusted")
    if "PasswordProtected" in text or "locked" in low:
        return BridgeError("not_trusted", "Buka kunci iPhone dan biarkan terbuka.")
    if name == "UserspaceTunnelUnavailableError" or "CoreDeviceProxy" in text or name == "InvalidServiceError":
        return BridgeError("unsupported_ios", "Tunnel layar tidak terbuka. Butuh iOS 17.4+, Trust, dan Developer Mode.")
    return BridgeError("unknown", f"{name}: {text}" if text else name)


def parse_version(text: str) -> tuple[int, ...]:
    out = []
    for part in text.split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def check_ios_version(version: str) -> None:
    if parse_version(version) < (17, 4, 0):
        raise BridgeError("unsupported_ios", f"iOS {version} terlalu lama. Butuh iOS 17.4 atau lebih baru.")


def watch_stdin(stream, on_eof) -> threading.Thread:
    """Call on_eof when the parent closes our stdin (it exited or crashed). Data is ignored.

    This is how an orphaned bridge dies on every platform, with no pid polling.
    """
    def pump() -> None:
        try:
            while stream.read(1024):
                pass
        except (OSError, ValueError):
            pass
        on_eof()

    t = threading.Thread(target=pump, daemon=True, name="stdin-watch")
    t.start()
    return t


def parent_gone(loop, task, grace: float = 3.0) -> None:
    """The client closed our stdin (it exited or crashed): shut down.

    With a running asyncio task, cancel it so the tunnel/HID/display cleanup in finally blocks runs;
    os._exit is only the fallback if that takes longer than `grace`. Without a task, exit at once.
    """
    try:
        emit("status", phase="stopped", reason="parent_gone")
    finally:
        if loop is None or task is None:
            os._exit(0)
        timer = threading.Timer(grace, os._exit, args=(0,))
        timer.daemon = True
        timer.start()
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # loop already closed
            os._exit(0)


def install_parent_watch(stream, grace: float = 3.0) -> threading.Thread:
    """Call from inside the running asyncio task that should be cancelled on parent exit."""
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    return watch_stdin(stream, lambda: parent_gone(loop, task, grace))


def default_log_path():
    """Per-user log location: %LOCALAPPDATA%/StrikeMirror/logs on Windows, XDG state dir elsewhere."""
    from pathlib import Path

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "StrikeMirror"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "strikemirror"
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "bridge.log"


def setup_logging(path: str, max_bytes: int = 2_000_000, backups: int = 3):
    """Send Python logging (including pymobiledevice3) to a size-limited rotating file.

    stdout stays reserved for protocol events, so logs must never go there.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def pick_device(udid: str | None) -> str:
    from pymobiledevice3.usbmux import list_devices

    devices = [d for d in await list_devices() if getattr(d, "connection_type", "USB") == "USB"]
    serials = [d.serial for d in devices]
    if udid:
        if udid not in serials:
            raise BridgeError("device_not_found")
        return udid
    if not serials:
        raise BridgeError("device_not_found")
    if len(serials) > 1:
        raise BridgeError("multiple_devices")
    return serials[0]


async def preflight(udid: str) -> str:
    """Return the iOS version after checking Trust, version and Developer Mode."""
    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = await create_using_usbmux(serial=udid, autopair=False)
    try:
        version = str(lockdown.product_version)
        check_ios_version(version)
        if not await lockdown.get_developer_mode_status():
            raise BridgeError("developer_mode_off")
        return version
    finally:
        await lockdown.close()


async def run(args) -> None:
    if args.exit_on_stdin_eof:
        install_parent_watch(sys.stdin)
    from pymobiledevice3.remote.core_device.display_service import is_media_in_use_error
    from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer
    from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
    from pymobiledevice3.services.mobile_image_mounter import auto_mount

    emit("status", phase="detecting")
    udid = await pick_device(args.udid)
    emit("status", phase="checking", udid=udid)
    ios = await preflight(udid)

    password = os.environ.get("STRIKEMIRROR_BRIDGE_PASSWORD") or secrets.token_urlsafe(18)
    port = args.port or free_port()

    for attempt in range(2):
        emit("status", phase="tunnel")
        async with PreferredRsdTunnel(serial=udid, autopair=True) as rsd:
            if DISPLAY_SERVICE not in (rsd.peer_info or {}).get("Services", {}):
                if attempt:
                    raise BridgeError("display_service_missing")
                emit("status", phase="mounting", note="Memasang developer image (unduh pertama kali).")
                await auto_mount(rsd)
                continue

            server = ScreenStreamServer(
                rsd,
                bind="127.0.0.1",
                http_port=port,
                password=password,
                audio_default_on=not args.no_audio,
                max_bitrate_kbps=args.max_bitrate,
                motion_idr=not args.no_motion_idr,
            )
            if args.no_audio:
                async def video_only(original=server._ensure_fresh_stream) -> None:
                    try:
                        await original(force=False)
                    except Exception as exc:  # retried lazily on /codec
                        if is_media_in_use_error(exc):
                            raise

                server._eager_stream_start = video_only
            emit("ready", **ready_event(port, password, udid, ios))
            await server.serve()
            return


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="StrikeMirror iOS bridge")
    p.add_argument("--udid")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--exit-on-stdin-eof", action="store_true", help="exit when the parent closes stdin")
    p.add_argument("--log-file", help="rotating log file (default: per-user log directory)")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--no-motion-idr", action="store_true")
    p.add_argument("--max-bitrate", type=int, default=60000, help="kbps cap advertised to the iPhone")
    args = p.parse_args(argv)
    if not args.no_log:
        try:
            setup_logging(args.log_file or str(default_log_path()))
        except OSError:
            pass  # logging is best effort; never block mirroring
    try:
        asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except BaseException as exc:  # noqa: BLE001 - report everything as a protocol event
        err = classify(exc)
        emit("error", code=err.code, message=str(err))
        return 2
    emit("status", phase="stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
