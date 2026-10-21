"""scrcpy-like window for iPhone: python client/player.py [--udid UDID]

Mouse = finger (right click = Home). Keyboard goes to the iPhone as a hardware keyboard.
Ctrl+A/C/V/X/Z -> Cmd+key (Ctrl+C/V also sync the clipboard). F1 Home, F2 Lock, F3 Vol-, F4 Vol+,
F5 screenshot, F6/F7 rotate, F8 restart stream, F9 record on/off, mouse wheel = scroll.
Needs: Pillow (pip install pillow); tkinter ships with Python on Windows.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ios_client import BridgeError, IOSDevice  # noqa: E402
from player_logic import FUNCTION_ACTIONS, KeyState, fit_rect, scroll_swipe, window_to_norm  # noqa: E402

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    sys.exit("Pasang Pillow dulu: pip install pillow")


class Player:
    def __init__(self, dev: IOSDevice, title: str):
        self.dev = dev
        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("390x844")
        self.root.configure(bg="black")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.img_id = self.canvas.create_image(0, 0, anchor="nw")
        self.photo = None
        self.rect = (0, 0, 1, 1)
        self.last_frames = 0
        self.fps_t, self.fps_n = time.time(), 0
        self.keys = KeyState()
        self.title = title
        c = self.canvas
        c.bind("<ButtonPress-1>", lambda e: self._touch("down", e))
        c.bind("<B1-Motion>", lambda e: self._touch("move", e))
        c.bind("<ButtonRelease-1>", lambda e: self._touch("up", e))
        c.bind("<ButtonPress-3>", lambda e: dev.input.button("home"))
        c.bind("<MouseWheel>", self._wheel)
        self.recording = False
        self.root.bind("<KeyPress>", self._key_down)
        self.root.bind("<KeyRelease>", self._key_up)
        self.root.bind("<FocusOut>", lambda e: dev.input.keys(self.keys.clear()))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(5, self._tick)

    def _touch(self, kind: str, e) -> None:
        n = window_to_norm(e.x, e.y, self.rect)
        if n is None:
            return
        inp = self.dev.input
        {"down": inp.down, "move": inp.move, "up": inp.up}[kind](0, *n)

    def _wheel(self, e) -> None:
        n = window_to_norm(e.x, e.y, self.rect)
        if n is None:
            return
        x1, y1, x2, y2 = scroll_swipe(n[0], n[1], e.delta)
        threading.Thread(target=self.dev.input.swipe, args=(x1, y1, x2, y2, 120, 6), daemon=True).start()

    def _key_down(self, e) -> None:
        act = FUNCTION_ACTIONS.get(e.keysym)
        if act:
            return self._action(act)
        ctrl = bool(e.state & 0x4)
        if ctrl and e.keysym.lower() == "v":
            try:
                self.dev.clipboard_set(self.root.clipboard_get())
            except tk.TclError:
                pass
        report = self.keys.press(e.keysym, ctrl=ctrl)
        if report is not None:
            self.dev.input.keys(report)
        if ctrl and e.keysym.lower() in ("c", "x"):
            self.root.after(250, self._pull_clipboard)

    def _key_up(self, e) -> None:
        report = self.keys.release(e.keysym)
        if report is not None:
            self.dev.input.keys(report)

    def _pull_clipboard(self) -> None:
        text = (self.dev.clipboard_get() or {}).get("text")
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _action(self, act: str) -> None:
        if act in ("home", "lock", "volume-down", "volume-up"):
            self.dev.input.button(act)
        elif act == "screenshot":
            path = f"ios_{int(time.time())}.png"
            print("screenshot:", path if self.dev.screenshot(path) else "belum ada frame")
        elif act.startswith("rotate"):
            self.dev.rotate(act.split("-")[1])
        elif act == "restart":
            self.dev.restart_stream()
        elif act == "record":
            if self.recording:
                self.dev.video.stop_recording()
                print("rekam: selesai")
            else:
                path = f"ios_{int(time.time())}.mp4"
                self.dev.video.start_recording(path)
                print("rekam: mulai ->", path)
            self.recording = not self.recording

    def _tick(self) -> None:
        frames, f = self.dev.video.wait_frame(self.last_frames, 0.0)
        if f is not None and frames != self.last_frames:
            self.last_frames = frames
            self.fps_n += 1
            w, h = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
            self.rect = fit_rect(w, h, f.width, f.height)
            x, y, rw, rh = self.rect
            img = f.to_image().resize((rw, rh), Image.BILINEAR)
            self.photo = ImageTk.PhotoImage(img)
            self.canvas.itemconfig(self.img_id, image=self.photo)
            self.canvas.coords(self.img_id, x, y)
        now = time.time()
        if now - self.fps_t >= 1:
            self.root.title(f"{self.title}  {self.fps_n} fps")
            self.fps_t, self.fps_n = now, 0
        self.root.after(4, self._tick)

    def close(self) -> None:
        self.dev.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udid")
    ap.add_argument("--no-audio", action="store_true", default=True)
    args = ap.parse_args()
    dev = IOSDevice(args.udid, on_status=lambda ev: print("·", ev.get("phase"), ev.get("note") or ""),
                    extra_args=["--no-audio"] if args.no_audio else [],
                    on_event=lambda ev: print("!", ev.get("event"), ev.get("code") or ""))
    try:
        info = dev.connect()
    except BridgeError as exc:
        print(f"[{exc.code}] {exc}")
        return 1
    Player(dev, f"StrikeMirror iOS — {info.ios}").run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
