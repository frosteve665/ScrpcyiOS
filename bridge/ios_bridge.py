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
import json
import os
import secrets
import socket
import sys

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
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


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
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--no-motion-idr", action="store_true")
    p.add_argument("--max-bitrate", type=int, default=60000, help="kbps cap advertised to the iPhone")
    args = p.parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
    except BaseException as exc:  # noqa: BLE001 - report everything as a protocol event
        err = classify(exc)
        emit("error", code=err.code, message=str(err))
        return 2
    emit("status", phase="stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
