#!/usr/bin/env python3
"""kbini2board – turn the KB.ini of a BYCOMBO4 vendor app (OemDrv.exe, Fw=24) into a board file

The AULA, Womier, LEOBOG ... apps built on OemDrv.exe describe each keyboard in
Dev/kb/<name>/KB.ini: USB ids, the device id the board answers ("Psd"), the matrix size, the
effect list (LedOpt), the key rectangles of the picture with their matrix slot, and the factory
Fn layer. This reads one or more of them (variants of the same board: wired, KR, ...) and
prints a board file for facet-keyboard (sinowealth-g5kb family) on stdout.

  tools/kbini2board.py --id aula-f65 --vendor AULA --source "AULA F65 driver.exe (aulastar.com)" KB.ini [more KB.ini]

The layout rows come from the key rectangles: keys whose centres lie on one line form a row,
widths and gaps are measured in key pitches and rounded to quarter units. Check the result
against a picture of the board; the vendor pictures are not always to scale.
"""
import argparse
import json
import re
import statistics
import sys

# Windows virtual-key codes in KB.ini -> facet-keyboard key ids (xkb names, lower case)
VK = {
    0x08: "backspace", 0x09: "tab", 0x0D: "return", 0x13: "pause", 0x14: "caps_lock", 0x1B: "esc",
    0x20: "space", 0x21: "prior", 0x22: "next", 0x23: "end", 0x24: "home", 0x25: "left",
    0x26: "up", 0x27: "right", 0x28: "down", 0x2C: "print", 0x2D: "insert", 0x2E: "delete",
    **{0x30 + i: str(i) for i in range(10)},
    **{0x41 + i: chr(ord("a") + i) for i in range(26)},
    0x5B: "super_l", 0x5C: "super_r", 0x5D: "menu",
    **{0x60 + i: f"kp_{i}" for i in range(10)},
    0x6A: "kp_multiply", 0x6B: "kp_add", 0x6D: "kp_subtract", 0x6E: "kp_decimal", 0x6F: "kp_divide",
    **{0x70 + i: f"f{i + 1}" for i in range(12)},
    0x90: "num_lock", 0x91: "scroll_lock",
    0xA0: "shift_l", 0xA1: "shift_r", 0xA2: "control_l", 0xA3: "control_r", 0xA4: "alt_l", 0xA5: "alt_r",
    0xBA: "semicolon", 0xBB: "equal", 0xBC: "comma", 0xBD: "minus", 0xBE: "period", 0xBF: "slash",
    0xC0: "grave", 0xDB: "bracketleft", 0xDC: "backslash", 0xDD: "bracketright", 0xDE: "apostrophe",
    0xE2: "less",        # VK_OEM_102, the extra ISO key next to left shift
    0xFA: "fn",          # the app's own code for Fn
    0xFD: "kp_enter",    # the app's own code for the second Enter (numpad)
    0x99: "calc",        # the app's own code; a calculator key above the numpad
    0xC4: "hangul",      # Korean boards: the key in place of right Ctrl
}
# Key id -> HID usage (keyboard page), for [FN1] entries of type 0x02 (a VK code)
HID = {**{chr(ord("a") + i): 0x04 + i for i in range(26)},
       **{str((i + 1) % 10): 0x1E + i for i in range(10)},
       "return": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, "minus": 0x2D,
       "equal": 0x2E, "bracketleft": 0x2F, "bracketright": 0x30, "backslash": 0x31,
       "semicolon": 0x33, "apostrophe": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37,
       "slash": 0x38, "caps_lock": 0x39, **{f"f{i}": 0x39 + i for i in range(1, 13)},
       "print": 0x46, "scroll_lock": 0x47, "pause": 0x48, "insert": 0x49, "home": 0x4A,
       "prior": 0x4B, "delete": 0x4C, "end": 0x4D, "next": 0x4E, "right": 0x4F, "left": 0x50,
       "down": 0x51, "up": 0x52, "num_lock": 0x53, "less": 0x64, "menu": 0x65,
       "control_l": 0xE0, "shift_l": 0xE1, "alt_l": 0xE2, "super_l": 0xE3,
       "control_r": 0xE4, "shift_r": 0xE5, "alt_r": 0xE6, "super_r": 0xE7}
VK_HID = {vk: HID[name] for vk, name in VK.items() if name in HID}
# Firmware effect number (first LedOpt column) -> name; the same numbers on every board of
# the family (names from the L65 firmware table)
EFFECT_NAMES = {
    0: "off", 1: "static", 2: "breathing", 3: "rainbow", 4: "flash-away", 5: "raindrops",
    6: "rainbow-wheel", 7: "ripples", 8: "stars", 9: "shadow", 10: "snake", 11: "neon-stream",
    12: "reaction", 13: "sine-wave", 14: "scanning", 15: "windmill", 16: "waterfall",
    17: "blossoming", 18: "storm", 21: "self-define",
}


def read_ini(path):
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    sections, cur = {}, None
    for line in text.replace("\r", "").split("\n"):
        line = line.split("//")[0].strip() if line.lstrip().startswith("//") else line.strip()
        if not line or line.startswith(";"):
            continue
        m = re.fullmatch(r"\[(.+)\]", line)
        if m:
            cur = sections.setdefault(m.group(1).upper(), [])
            continue
        if cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur.append((k.strip(), v.strip()))
    return sections


def num(text):
    text = text.strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def opt(sections, key, default=None):
    for k, v in sections.get("OPT", []):
        if k.lower() == key.lower():
            return v
    return default


def keys_of(sections):
    """[(K index, name, slot, rect, default)]; rect is None for keys without a picture (knob)"""
    out = []
    for k, v in sections.get("KEY", []):
        m = re.fullmatch(r"K(\d+)", k)
        if not m:
            continue
        f = [x.strip() for x in v.split(",")]
        x1, y1, x2, y2 = (num(x) for x in f[:4])
        typ, code, slot = num(f[4]), f[5], num(f[-1])
        if typ == 0x09:
            name, default = "knob", num(f[6])
        else:
            name, default = VK.get(num(code)), None
            if name is None:
                print(f"warning: K{m.group(1)}: unknown VK {code}, left out", file=sys.stderr)
                continue
        rect = (x1, y1, x2, y2) if x2 > x1 else None
        out.append((int(m.group(1)), name, slot, rect, default))
    return out


def layout_rows(keys):
    """Rows of [key id, width in units] with gaps as ["", width], from the key rectangles"""
    drawn = [k for k in keys if k[3]]
    widths = [k[3][2] - k[3][0] for k in drawn]
    unit_w = statistics.mode(widths)
    unit_h = statistics.mode(k[3][3] - k[3][1] for k in drawn)
    # Rows by the top edge, so a key two rows tall (numpad + and Enter) stays in its upper row
    # and leaves a gap in the one below
    lines = []
    for k in sorted(drawn, key=lambda k: k[3][1]):
        cy = k[3][1]
        if lines and abs(lines[-1][0] - cy) < unit_h * 0.5:
            lines[-1][1].append(k)
        else:
            lines.append([cy, [k]])
    # Pitch: distance between neighbouring 1u keys
    steps = []
    for _, row in lines:
        row.sort(key=lambda k: k[3][0])
        for a, b in zip(row, row[1:]):
            if a[3][2] - a[3][0] == unit_w and b[3][2] - b[3][0] == unit_w:
                steps.append(b[3][0] - a[3][0])
    pitch = statistics.median(steps)
    gap = pitch - unit_w
    left = min(k[3][0] for k in drawn)
    q = lambda v: round(v * 4) / 4
    rows = []
    for _, row in lines:
        out, x = [], left
        for k in row:
            space = q((k[3][0] - x) / pitch)
            if space >= 0.25:
                out.append(["", space])
            w = q((k[3][2] - k[3][0] + gap) / pitch)
            out.append([k[1], w])
            x = k[3][2] + gap
        rows.append(out)
    # Keys without a picture (the knob) go to the end of the top row
    for k in keys:
        if not k[3]:
            rows[0] += [["", 0.5], [k[1], 1]]
    return rows


def entry_hex(text):
    v = num(text)
    return f"0x{v:08x}"


def fn_defaults(sections, keys):
    by_index = {k[0]: k[1] for k in keys}
    out = {}
    for k, v in sections.get("FN1", []):
        m = re.fullmatch(r"K(\d+)", k)
        if not m or int(m.group(1)) not in by_index:
            continue
        f = [x.strip() for x in v.split(",")]
        typ = num(f[0])
        if typ == 0x09:
            out[by_index[int(m.group(1))]] = entry_hex(f[2])
        elif typ == 0x02 and num(f[1]) in VK_HID:
            out[by_index[int(m.group(1))]] = f"0x{VK_HID[num(f[1])]:08x}"
        else:
            print(f"warning: [FN1] {k}={v}: not converted", file=sys.stderr)
    # The knob's line in [KEY] carries its own action; Fn+knob does the same on the factory board
    for _, name, _, _, default in keys:
        if name == "knob" and default is not None and name not in out:
            out[name] = f"0x{default:08x}"
    return out


def effects(sections):
    out = []
    for k, v in sections.get("OPT", []):
        m = re.fullmatch(r"LedOpt(\d+)", k)
        if not m:
            continue
        hw, _ui, speed, _light, direction, _random, color = (num(x) for x in v.split(","))
        out.append([int(m.group(1)), hw, EFFECT_NAMES.get(hw, f"effect-{hw}"), bool(speed), bool(color)])
        if direction:
            print(f"note: {k} has a direction, the tool does not set it", file=sys.stderr)
    return sorted(out)


def psd(sections):
    return " ".join(f"{int(x, 16):02x}" for x in opt(sections, "Psd").split(","))


def levels(sections, key):
    ui = [num(x) for x in opt(sections, key, "0,1,2,3,4").split(",")]
    hw = [num(x) for x in opt(sections, key + "HW", ",".join(map(str, ui))).split(",")]
    return ui, hw


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--id", required=True)
    p.add_argument("--vendor", required=True)
    p.add_argument("--name", help="display name (default: Name= of the first file)")
    p.add_argument("--source", required=True, help="where the KB.ini came from (file name, version, site)")
    p.add_argument("--notes", help="docs/boards/<file>.md")
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--row", action="append", default=[], metavar="N=JSON",
                   help='replace layout row N (0 = top) by hand, e.g. 0=[["esc",1],["",0.5],["f1",1]]')
    p.add_argument("ini", nargs="+")
    a = p.parse_args()

    first = read_ini(a.ini[0])
    keys = keys_of(first)
    slots = {name: slot for _, name, slot, _, _ in keys}
    ids, usb = {}, []
    for path in a.ini:
        s = read_ini(path)
        ids[psd(s)] = opt(s, "Name")
        other = {name: slot for _, name, slot, _, _ in keys_of(s)}
        # A variant may have a key more (the ISO key of a German edition): it joins the key
        # list, the drawn layout stays the first file's. A key on another slot is an error.
        for name, slot in other.items():
            if name in slots and slots[name] != slot:
                sys.exit(f"{path}: {name} is on slot {slot}, in {a.ini[0]} on {slots[name]}; not one board")
            if name not in slots:
                if slot in slots.values():
                    sys.exit(f"{path}: {name} is on slot {slot}, which is another key in {a.ini[0]}; not one board")
                print(f"note: {path}: extra key {name} on slot {slot}", file=sys.stderr)
                slots[name] = slot
        for kind, vk, pk in (("wired", "VID", "PID"), ("dongle", "VID_Wireless", "PID_Wireless")):
            vid, pid = num(opt(s, vk, "0")), num(opt(s, pk, "0"))
            if vid and {"vid": f"{vid:04x}", "pid": f"{pid:04x}", "link": kind} not in usb:
                usb.append({"vid": f"{vid:04x}", "pid": f"{pid:04x}", "link": kind})
    light_ui, light_hw = levels(first, "Light")
    speed_ui, speed_hw = levels(first, "Speed")
    fn_slot = slots.get("fn")
    board = {
        "id": a.id,
        "name": a.name or opt(first, "Name"),
        "vendor": a.vendor,
        "aliases": a.alias,
        "family": "sinowealth-g5kb",
        "tested": False,
        "source": a.source,
        "notes": a.notes or "docs/boards/sinowealth-g5kb.md",
        "usb": usb,
        "can": {
            "effects": True, "brightness": len(light_ui) - 1, "speed": len(speed_ui) - 1,
            "perkey": True, "live": True, "fn": fn_slot is not None,
            "layers": ["base", "fn1", "fn2", "tap"],
        },
        "protocol": {
            "ids": ids,
            "matrix_len": num(opt(first, "MatrixLen", "128")),
            "brightness_hw": light_hw if light_hw != light_ui else None,
            "speed_hw": speed_hw if speed_hw != speed_ui else None,
        },
        "effects": effects(first),
        "keys": dict(sorted(slots.items(), key=lambda kv: kv[1])),
        "layout": layout_rows(keys),
        "fn_defaults": fn_defaults(first, keys),
    }
    board["protocol"] = {k: v for k, v in board["protocol"].items() if v is not None}
    for spec in a.row:
        n, _, row = spec.partition("=")
        board["layout"][int(n)] = json.loads(row)
    drawn = {k for row in board["layout"] for k, _ in row if k}
    if drawn - set(slots):
        sys.exit(f"layout names keys the board does not have: {sorted(drawn - set(slots))}")
    print(dump(board))


def dump(board):
    """JSON with one line per layout row, effect and short value, so the file stays readable"""
    c = lambda v: json.dumps(v, ensure_ascii=False)
    lines = ["{"]
    items = list(board.items())
    for i, (k, v) in enumerate(items):
        end = "," if i < len(items) - 1 else ""
        if k in ("layout", "effects"):
            lines.append(f"  {c(k)}: [")
            lines += [f"    {c(r)}{',' if j < len(v) - 1 else ''}" for j, r in enumerate(v)]
            lines.append(f"  ]{end}")
        elif isinstance(v, dict) and k in ("keys", "keymap", "fn_defaults") and v:
            pairs = [f"{c(a)}: {c(b)}" for a, b in v.items()]
            rows = [", ".join(pairs[j:j + 8]) for j in range(0, len(pairs), 8)]
            lines.append(f"  {c(k)}: {{")
            lines += [f"    {r}{',' if j < len(rows) - 1 else ''}" for j, r in enumerate(rows)]
            lines.append(f"  }}{end}")
        else:
            lines.append(f"  {c(k)}: {c(v)}{end}")
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
