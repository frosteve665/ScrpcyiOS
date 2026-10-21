"""Pure logic for the player window (no GUI imports) so it can be unit-tested headless."""
from __future__ import annotations

CMD = 0xE3
SHIFT = 0xE1
CMD_SHORTCUTS = {"a", "c", "v", "x", "z"}

_NAMED = {
    "Return": 0x28, "KP_Enter": 0x28, "Escape": 0x29, "BackSpace": 0x2A, "Tab": 0x2B, "space": 0x2C,
    "minus": 0x2D, "equal": 0x2E, "bracketleft": 0x2F, "bracketright": 0x30, "backslash": 0x31,
    "semicolon": 0x33, "apostrophe": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37, "slash": 0x38,
    "Delete": 0x4C, "Home": 0x4A, "End": 0x4D, "Prior": 0x4B, "Next": 0x4E,
    "Right": 0x4F, "Left": 0x50, "Down": 0x51, "Up": 0x52,
    "Shift_L": SHIFT, "Shift_R": 0xE5, "Alt_L": 0xE2, "Alt_R": 0xE6,
}
# shifted symbols reported by Tk as their own keysym -> (base key usage)
_SHIFTED = {"exclam": 0x1E, "at": 0x1F, "numbersign": 0x20, "dollar": 0x21, "percent": 0x22,
            "asciicircum": 0x23, "ampersand": 0x24, "asterisk": 0x25, "parenleft": 0x26, "parenright": 0x27,
            "underscore": 0x2D, "plus": 0x2E, "braceleft": 0x2F, "braceright": 0x30, "bar": 0x31,
            "colon": 0x33, "quotedbl": 0x34, "asciitilde": 0x35, "less": 0x36, "greater": 0x37, "question": 0x38}

FUNCTION_ACTIONS = {"F1": "home", "F2": "lock", "F3": "volume-down", "F4": "volume-up",
                    "F5": "screenshot", "F6": "rotate-left", "F7": "rotate-right", "F8": "restart", "F9": "record",
                    "F11": "fullscreen"}

# ---- MOD shortcuts, modelled on scrcpy's doc/shortcuts.md (Android-only keys omitted: Back,
# App switch, Menu, notification panels). Default MOD is Alt, like scrcpy's left Alt.
MOD_MASK_WINDOWS = 0x20000   # Tk <state> bit for Alt on Windows
MOD_MASK_X11 = 0x0008        # Mod1 (Alt) on X11

_MOD_ACTIONS = {
    "q": "quit", "f": "fullscreen",
    "left": "rotate-left", "right": "rotate-right", "r": "rotate-right",
    "h": "home", "p": "lock", "up": "volume-up", "down": "volume-down",
    "g": "window-1to1", "w": "window-fit", "i": "fps", "v": "paste",
}


def mod_pressed(state: int, mask: int) -> bool:
    return bool(state & mask)


def mod_action(keysym: str, shift: bool) -> str:
    """Action for MOD+key. Unlisted combos return "none": consumed, never typed into the iPhone."""
    key = keysym.lower()
    if shift and key == "r":
        return "restart"
    if shift:
        return "none"
    return _MOD_ACTIONS.get(key, "none")


def keysym_to_usage(keysym: str):
    """Return (usage, needs_shift) or (None, False)."""
    if keysym in _NAMED:
        return _NAMED[keysym], False
    if keysym in _SHIFTED:
        return _SHIFTED[keysym], True
    if len(keysym) == 1:
        ch = keysym.lower()
        if "a" <= ch <= "z":
            return 0x04 + ord(ch) - ord("a"), keysym.isupper()
        if "1" <= ch <= "9":
            return 0x1E + ord(ch) - ord("1"), False
        if ch == "0":
            return 0x27, False
    return None, False


def fit_rect(win_w: int, win_h: int, frame_w: int, frame_h: int):
    """Letterbox rect (x, y, w, h) that keeps the frame's aspect inside the window."""
    if min(win_w, win_h, frame_w, frame_h) <= 0:
        return 0, 0, max(win_w, 1), max(win_h, 1)
    scale = min(win_w / frame_w, win_h / frame_h)
    w, h = max(1, int(frame_w * scale)), max(1, int(frame_h * scale))
    return (win_w - w) // 2, (win_h - h) // 2, w, h


def window_to_norm(px: float, py: float, rect, clamp: bool = True):
    """Window pixel -> normalized (0..1) frame coords, or None if outside and not clamping."""
    x, y, w, h = rect
    nx, ny = (px - x) / w, (py - y) / h
    inside = 0 <= nx <= 1 and 0 <= ny <= 1
    if not inside and not clamp:
        return None
    return min(1.0, max(0.0, nx)), min(1.0, max(0.0, ny))


class KeyState:
    """Tracks held keys and yields the full HID usage set to send (like a real keyboard report)."""

    def __init__(self):
        self.held: dict[str, int] = {}
        self.cmd = False

    def press(self, keysym: str, ctrl: bool = False, shift: bool = False):
        usage, needs_shift = keysym_to_usage(keysym)
        if usage is None:
            return None
        if ctrl and keysym.lower() in CMD_SHORTCUTS:
            self.cmd = True
        elif ctrl:
            return None  # other Ctrl combos stay local
        self.held[keysym] = usage
        return self.report(extra_shift=needs_shift)

    def release(self, keysym: str):
        if keysym not in self.held and not self.cmd:
            return None
        self.held.pop(keysym, None)
        if not any(k.lower() in CMD_SHORTCUTS for k in self.held):
            self.cmd = False
        return self.report()

    def report(self, extra_shift: bool = False) -> list[int]:
        out = list(dict.fromkeys(self.held.values()))
        if self.cmd:
            out.insert(0, CMD)
        if extra_shift and SHIFT not in out:
            out.insert(0, SHIFT)
        return out

    def clear(self) -> list[int]:
        self.held.clear()
        self.cmd = False
        return []


def scroll_swipe(x: float, y: float, delta: int, step: float = 0.12, max_len: float = 0.6, margin: float = 0.02):
    """Mouse wheel -> a vertical finger swipe (normalized). Wheel down moves the finger up (content scrolls down).

    `delta` is notches (small ints) or Windows Tk units (multiples of 120). Returns (x1, y1, x2, y2).
    """
    notches = max(1, round(abs(delta) / 120)) if abs(delta) >= 120 else max(1, abs(int(delta)))
    length = min(step * notches, max_len)
    half = length / 2
    cy = min(max(y, margin + half), 1 - margin - half)
    top, bottom = cy - half, cy + half
    return (x, bottom, x, top) if delta < 0 else (x, top, x, bottom)
