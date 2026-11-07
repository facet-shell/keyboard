"""Shared parts of facet-keyboard: the board registry, finding the keyboard, hidraw access,
key names, colors and the dry-run printout. The protocol of each board family lives in
families/<family>.py, what each board has (ids, keys, effects, layout) in boards/<id>.json.
"""
import datetime
import json
import os
import re
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
BOARDS_DIR = Path(os.environ.get("FACET_KEYBOARD_BOARDS", DATA_DIR / "boards"))
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "facet"
BACKUP_DIR = STATE_DIR / "keyboard"
CHOICE_FILE = BACKUP_DIR / "board"
# The theme palettes live with the facet command (the cli repo); its link in ~/.local/bin
# leads there (the frame link is what older installs have)
CLI_DIR = next((Path(os.path.realpath(Path.home() / ".local/bin" / n)).parent
                for n in ("facet", "frame") if (Path.home() / ".local/bin" / n).exists()),
               Path.home() / ".local/share/facet/cli")
# Used while nothing is picked and nothing is plugged in: the board facet started with
DEFAULT_BOARD = "aula-f75"
REQUEST_URL = "https://github.com/orgs/facet-shell/projects/1"


class Fail(Exception):
    pass


# ---------------------------------------------------------------- board registry

class Board:
    """One boards/<id>.json; the fields are documented in docs/boards/README.md"""

    def __init__(self, data, path):
        self.data, self.path = data, path
        self.id = data["id"]
        self.name = data["name"]
        self.vendor = data.get("vendor", "")
        self.family = data["family"]
        self.tested = bool(data.get("tested"))
        self.can = data.get("can", {})
        self.protocol = data.get("protocol", {})
        self.keys = data.get("keys", {})
        self.layout = data.get("layout", [])
        self.usb = [(int(u["vid"], 16), int(u["pid"], 16), u.get("link", "wired")) for u in data.get("usb", [])]
        # USB ids that get a udev rule (an id borrowed from another vendor may opt out)
        self.udev = [(int(u["vid"], 16), int(u["pid"], 16), u.get("link", "wired"))
                     for u in data.get("usb", []) if u.get("udev", True)]

    def matches(self, text):
        hay = " ".join([self.id, self.name, self.vendor] + self.data.get("aliases", [])).lower()
        return all(w in hay for w in text.lower().split())

    def summary(self):
        return {"id": self.id, "name": self.name, "vendor": self.vendor, "family": self.family,
                "tested": self.tested, "aliases": self.data.get("aliases", []),
                "usb": [f"{v:04x}:{p:04x}" for v, p, _ in self.usb], "can": self.can,
                "notes": self.data.get("notes", "")}


def load_boards():
    out = {}
    for path in sorted(BOARDS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out[data["id"]] = Board(data, path)
        except (OSError, ValueError, KeyError) as e:
            raise Fail(f"bad board file {path.name}: {e}")
    return out


def saved_board():
    try:
        return CHOICE_FILE.read_text().strip() or None
    except OSError:
        return None


def save_board(board_id):
    CHOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHOICE_FILE.write_text(board_id + "\n")


# ---------------------------------------------------------------- devices

def usb_devices():
    """{(vid, pid), ...} of everything on the USB bus (sysfs, nothing is opened)"""
    out = set()
    for d in Path("/sys/bus/usb/devices").glob("*"):
        try:
            out.add((int((d / "idVendor").read_text(), 16), int((d / "idProduct").read_text(), 16)))
        except (OSError, ValueError):
            continue
    return out


def connected_boards(boards):
    """[(board, vid, pid, link)] for every registry USB id that is plugged in (not the ids a
    board borrows from another vendor)"""
    present = usb_devices()
    return [(b, v, p, link) for b in boards.values() for v, p, link in b.udev if (v, p) in present]


def hidraw_nodes(ids):
    """[(node, vid, pid, interface, report descriptor)] of hidraw nodes whose USB id is in ids;
    read from sysfs, no device is opened"""
    out = []
    for dev in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            uevent = (dev / "device/uevent").read_text()
        except OSError:
            continue
        m = re.search(r"HID_ID=\w+:0000([0-9A-F]{4}):0000([0-9A-F]{4})", uevent, re.I)
        if not m:
            continue
        vid, pid = int(m.group(1), 16), int(m.group(2), 16)
        if (vid, pid) not in ids:
            continue
        phys = re.search(r"HID_PHYS=\S*input(\d+)", uevent)
        try:
            desc = (dev / "device/report_descriptor").read_bytes()
        except OSError:
            desc = b""
        out.append((f"/dev/{dev.name}", vid, pid, phys.group(1) if phys else "?", desc))
    return out


def descriptor_has(desc, usage_page=None, report_id=None):
    """Rough check of a HID report descriptor: a vendor usage page item and a report ID item"""
    ok = True
    if usage_page is not None:
        ok &= bytes([0x06, usage_page & 0xFF, usage_page >> 8]) in desc
    if report_id is not None:
        ok &= bytes([0x85, report_id]) in desc
    return ok


def select_board(boards, wanted=None, quiet=False):
    """The board to talk to: --board, else what is plugged in (the saved choice wins when
    several boards share a USB id), else the saved choice, else the default"""
    note = (lambda msg: None) if quiet else (lambda msg: print(f"note: {msg}"))
    if wanted:
        if wanted not in boards:
            raise Fail(f"unknown board '{wanted}' (facet-keyboard boards)")
        return boards[wanted]
    saved = saved_board()
    if saved and saved not in boards:
        note(f"the saved board '{saved}' is not in the registry any more")
        saved = None
    found = []
    for b, *_ in connected_boards(boards):
        if b not in found:
            found.append(b)
    if found:
        if saved and boards[saved] in found:
            return boards[saved]
        if len(found) > 1:
            note(f"{len(found)} boards use the USB id that is plugged in ({', '.join(b.id for b in found)}); "
                 f"taking {found[0].id}. Pick yours: facet keyboard set <board>")
        return found[0]
    if saved:
        return boards[saved]
    note(f"no board picked yet, using {DEFAULT_BOARD} (facet keyboard set <board>)")
    return boards[DEFAULT_BOARD]


# ---------------------------------------------------------------- hidraw transport

def _ioc(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr   # _IOC(READ|WRITE, 'H', nr, size)


class Hidraw:
    """One hidraw node. In a dry run nothing is opened or sent; show() prints what would go out."""

    def __init__(self, live, node, verbose=False):
        self.live, self.verbose, self.node, self.fd = live, verbose, node, None
        if live:
            try:
                self.fd = os.open(node, os.O_RDWR)
            except PermissionError:
                raise Fail(f"no access to {node}: install the udev rule: facet update keyboard")

    def show(self, what, buf, header, payload, title=None):
        """Print a packet: the first `header` bytes as the header, then `payload` data bytes;
        title says what kind of report it is (default: SET_FEATURE, its ID and length)"""
        if self.live and not self.verbose:
            return
        tag = "send" if self.live else "dry-run"
        print(f"[{tag}] {what}: {title or f'SET_FEATURE report {buf[0]:02x}, {len(buf)} bytes'}")
        print("    header " + bytes(buf[:header]).hex(" "))
        data = bytes(buf[header:header + payload])
        for off in range(0, len(data), 16):
            row = data[off:off + 16]
            if any(row):
                print(f"    +{off:03x}  " + row.hex(" "))
        if payload and not any(data):
            print("    data all zero")

    def set_feature(self, buf, tries=3):
        import fcntl                       # Linux only; a dry run never gets here
        for attempt in range(tries):
            time.sleep(0.02)
            try:
                fcntl.ioctl(self.fd, _ioc(0x06, len(buf)), bytes(buf))
                return
            except OSError as e:
                if attempt == tries - 1:
                    raise Fail(f"SET_FEATURE failed: {e}")
                time.sleep(0.07)

    def get_feature(self, report_id, length, tries=3):
        import fcntl
        resp = bytearray(length)
        for attempt in range(tries):
            resp[0] = report_id
            try:
                fcntl.ioctl(self.fd, _ioc(0x07, length), resp)
                return bytes(resp)
            except OSError as e:
                if attempt == tries - 1:
                    raise Fail(f"GET_FEATURE failed: {e}")
                time.sleep(0.07)

    def write(self, buf):
        """An output report (interrupt OUT or SET_REPORT), report ID in byte 0"""
        try:
            os.write(self.fd, bytes(buf))
        except OSError as e:
            raise Fail(f"write to {self.node} failed: {e}")

    def read(self, length, timeout=1.0):
        """One input report, or None after timeout seconds"""
        import select
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self.fd, length)


def save_backup(kind, data, live, board_id):
    if not live:
        print(f"[dry-run] would back up the current {kind} ({len(data)} bytes) first")
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"{stamp}-{board_id}-{kind}.bin"
    path.write_bytes(data)
    print(f"backup: {path}")
    return path


# ---------------------------------------------------------------- key names

ALIASES = {
    "escape": "esc", "caps": "caps_lock", "shift": "shift_l", "lshift": "shift_l",
    "ctrl": "control_l", "control": "control_l", "lctrl": "control_l", "rctrl": "control_r",
    "super": "super_l", "win": "super_l", "lwin": "super_l", "meta": "super_l", "rwin": "super_r",
    "alt": "alt_l", "lalt": "alt_l", "ralt": "alt_r", "altgr": "alt_r", "iso_level3_shift": "alt_r",
    "rshift": "shift_r", "enter": "return", "del": "delete", "ins": "insert",
    "pgup": "prior", "page_up": "prior", "pgdn": "next", "page_down": "next",
    "wheel": "knob", "print_screen": "print", "sys_req": "print",
}
# xkb keysym names of the German layout -> US name of the same physical key
LAYOUT_DE = {
    "z": "y", "y": "z", "ssharp": "minus", "dead_acute": "equal", "acute": "equal",
    "udiaeresis": "bracketleft", "plus": "bracketright", "odiaeresis": "semicolon",
    "adiaeresis": "apostrophe", "minus": "slash",
    "numbersign": "backslash",   # guess: an ANSI board has # on the backslash key; ISO slots are unknown
    "dead_circumflex": "grave", "asciicircum": "grave",
}
# Key labels for the map in the app, US and (where different) German
LABELS_US = {
    "esc": "esc", "grave": "`", "minus": "-", "equal": "=", "backspace": "⌫", "delete": "del",
    "insert": "ins", "home": "home", "end": "end", "prior": "pgup", "next": "pgdn", "tab": "tab",
    "bracketleft": "[", "bracketright": "]", "backslash": "\\", "caps_lock": "caps",
    "semicolon": ";", "apostrophe": "'", "return": "⏎", "shift_l": "shift", "shift_r": "shift",
    "comma": ",", "period": ".", "slash": "/", "control_l": "ctrl", "control_r": "ctrl",
    "super_l": "super", "super_r": "super", "alt_l": "alt", "alt_r": "alt", "space": "space",
    "fn": "fn", "menu": "menu", "up": "↑", "down": "↓", "left": "←", "right": "→", "knob": "◉",
    "print": "prt", "scroll_lock": "scr", "pause": "pause", "num_lock": "num", "less": "<",
    "iso_hash": "#", "hangul": "한/영", "calc": "calc", "kp_divide": "/", "kp_multiply": "*",
    "kp_subtract": "-", "kp_add": "+", "kp_enter": "⏎", "kp_decimal": ".",
    **{f"kp_{i}": str(i) for i in range(10)}, **{f"f{i}": f"F{i}" for i in range(1, 25)},
}
LABELS_DE = {
    "grave": "^", "minus": "ß", "equal": "´", "y": "Z", "z": "Y", "bracketleft": "Ü",
    "bracketright": "+", "backslash": "#", "semicolon": "Ö", "apostrophe": "Ä", "slash": "-",
    "alt_r": "altgr", "kp_decimal": ",",
}


def label(key, layout="us"):
    if layout == "de" and key in LABELS_DE:
        return LABELS_DE[key]
    return LABELS_US.get(key, key.upper() if len(key) == 1 else key)


def key_id(name, layout="us"):
    key = name.strip().lower()
    if layout == "de":
        key = LAYOUT_DE.get(key, key)
    return ALIASES.get(key, key)


# HID usages (keyboard page) by key id; what a key does in a remap
HID = {**{chr(ord("a") + i): 0x04 + i for i in range(26)},
       **{str((i + 1) % 10): 0x1E + i for i in range(10)},
       "return": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, "minus": 0x2D,
       "equal": 0x2E, "bracketleft": 0x2F, "bracketright": 0x30, "backslash": 0x31, "iso_hash": 0x32,
       "semicolon": 0x33, "apostrophe": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37,
       "slash": 0x38, "caps_lock": 0x39,
       **{f"f{i}": 0x39 + i for i in range(1, 13)},
       "print": 0x46, "scroll_lock": 0x47, "pause": 0x48, "insert": 0x49, "home": 0x4A,
       "prior": 0x4B, "delete": 0x4C, "end": 0x4D, "next": 0x4E, "right": 0x4F, "left": 0x50,
       "down": 0x51, "up": 0x52, "num_lock": 0x53, "kp_divide": 0x54, "kp_multiply": 0x55,
       "kp_subtract": 0x56, "kp_add": 0x57, "kp_enter": 0x58,
       **{f"kp_{(i + 1) % 10}": 0x59 + i for i in range(10)},
       "kp_decimal": 0x63, "less": 0x64, "menu": 0x65,
       **{f"f{i}": 0x68 + i - 13 for i in range(13, 25)},
       "control_l": 0xE0, "shift_l": 0xE1, "alt_l": 0xE2, "super_l": 0xE3,
       "control_r": 0xE4, "shift_r": 0xE5, "alt_r": 0xE6, "super_r": 0xE7}
HID_NAME = {}
for _name, _code in HID.items():
    HID_NAME.setdefault(_code, _name)
MODS = [("ctrl", 0x01), ("shift", 0x02), ("alt", 0x04), ("super", 0x08),
        ("rctrl", 0x10), ("rshift", 0x20), ("ralt", 0x40), ("rsuper", 0x80)]
MOD_ALIASES = {"control": "ctrl", "win": "super", "altgr": "ralt"}
# Consumer page usages for media actions
MEDIA = {"play": 0xCD, "next-track": 0xB5, "prev-track": 0xB6, "stop": 0xB7, "mute": 0xE2,
         "volup": 0xE9, "voldown": 0xEA, "mail": 0x18A, "calc": 0x192, "explorer": 0x194,
         "www": 0x223, "media": 0x183, "bright-up": 0x6F, "bright-down": 0x70}


def parse_combo(text, layout="us"):
    """'ctrl+alt+t' -> (modifier bits, [HID usages]); HID usages name physical keys, so a
    QWERTZ name is turned into the US name of the same key first"""
    parts = text.strip().lower().split("+")
    mod_bits, keys, mods = dict(MODS), [], 0
    for p in parts:
        p = MOD_ALIASES.get(p, p)
        if p in mod_bits and len(parts) > 1:
            mods |= mod_bits[p]
            continue
        p = key_id(p, layout)
        if p not in HID:
            return None
        keys.append(HID[p])
    return mods, keys


# ---------------------------------------------------------------- colors

NAMED_COLORS = {"red": "ff0000", "green": "00ff00", "blue": "0000ff", "white": "ffffff",
                "off": "000000", "black": "000000", "yellow": "ffff00", "cyan": "00ffff",
                "magenta": "ff00ff", "orange": "ff8000", "purple": "8000ff"}


def theme_colors(name=None):
    """The active facet theme's colors (theme roles -> #rrggbb); {} when there is none"""
    style = "tui"
    try:
        style = (STATE_DIR / "style").read_text().strip() or "tui"
    except OSError:
        pass
    if not name:
        for f in (STATE_DIR / f"theme-{style}", STATE_DIR / "theme"):
            try:
                name = f.read_text().strip()
                break
            except OSError:
                continue
    for path in (CLI_DIR / "themes" / style / f"{name or 'periwinkle'}.json",
                 CLI_DIR / "themes" / f"{name or 'periwinkle'}.json"):
        try:
            return json.loads(path.read_text())["colors"]
        except (OSError, KeyError, ValueError):
            continue
    return {}


def parse_color(text, theme):
    t = text.strip().lower()
    t = NAMED_COLORS.get(t, t)
    t = {k.lower(): v for k, v in theme.items()}.get(t, t).lower().lstrip("#")
    if not re.fullmatch(r"[0-9a-f]{6}", t):
        raise Fail(f"not a color: '{text}' (#rrggbb, a theme role or {', '.join(NAMED_COLORS)})")
    return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
