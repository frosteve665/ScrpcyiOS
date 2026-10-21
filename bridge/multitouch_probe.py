"""Multitouch experiment (GPL: imports pymobiledevice3). Run on the PC with the iPhone plugged in.

Open a map or photo on the iPhone first (anything that visibly reacts to two fingers).
Each variant sends a two-finger pinch for ~2 s. Watch the phone and answer when asked.
Safe: only synthetic touch events, same HID service the mirror already uses.

  python bridge/multitouch_probe.py [--udid UDID]   (tutup player.py dulu: stream layar tidak boleh dipakai dua klien)
"""
from __future__ import annotations

import argparse
import asyncio
import time

from mt_reports import VARIANTS, build_two_contact

CONTACT, RELEASE = 0xC2, 0x02
SERVICE_ID = 257  # mainTouchscreen


async def run(udid):
    from pymobiledevice3.remote.core_device.hid_service import touch_session
    from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel

    async with PreferredRsdTunnel(serial=udid, autopair=True) as rsd:
        async with touch_session(rsd) as svc:  # starts the media stream the HID path requires
            results = {}
            for variant in VARIANTS:
                print(f"\n== variant {variant}: pinch-out in the middle of the screen ==")
                steps = 40
                for i in range(steps + 1):
                    t = i / steps
                    a = (CONTACT, int(30000 - 12000 * t), int(32768 - 12000 * t))
                    b = (CONTACT, int(35500 + 12000 * t), int(32768 + 12000 * t))
                    await svc.send_report(SERVICE_ID, build_two_contact(variant, a, b, time.monotonic_ns()))
                    await asyncio.sleep(0.05)
                a = (RELEASE, 18000, 20768)
                b = (RELEASE, 47500, 44768)
                await svc.send_report(SERVICE_ID, build_two_contact(variant, a, b, time.monotonic_ns()))
                ans = input("Zoom/dua titik terlihat? [y=ya / n=tidak / s=satu jari saja]: ").strip().lower()
                results[variant] = ans
            print("\nHasil:", results)
            print("Kirim baris 'Hasil' di atas ke Claude.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--udid")
    asyncio.run(run(p.parse_args().udid))


if __name__ == "__main__":
    main()
