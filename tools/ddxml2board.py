#!/usr/bin/env python3
"""ddxml2board – a board file from the layout XML of a "DeviceDriver.exe" vendor app (sonix-sn32 family)

The AULA F75 Max app (and its siblings from the same OEM) describe the keyboard in
config.xml (USB ids) and layouts/<name>.xml: one <key> per key with its HID usage, the index
in the key map (key_index), the index of its LED (light_index) and its rectangle in the picture.
This prints a board file for facet-keyboard on stdout.

  tools/ddxml2board.py --id aula-f75-max --vendor AULA --name "AULA F75 Max" \
      --source "AULA F75 Max ... Driver-1.0.0.5.exe (aulastar.com)" config.xml layouts/rgb-keyboard.xml
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kbini2board import HID, dump, layout_rows  # noqa: E402

HID_ID = {v: k for k, v in HID.items()}
HID_ID[0xAF] = "fn"        # the app's own code for Fn

# The app's built-in effect table (FUN_0042b380 in DeviceDriver.exe 1.0.0.5): mode value,
# name (string ids 521-540 of the language files), feature bits. Bit 1 speed, bits 2/3
# direction, bit 4 color; bit 0 (brightness) and bit 5 (colorful) are on for all but off.
EFFECTS = [
    (1, "static", 0x31), (2, "single-on", 0x33), (3, "single-off", 0x33), (4, "glittering", 0x33),
    (5, "falling", 0x33), (6, "colourful", 0x03), (7, "breathing", 0x33), (8, "spectrum", 0x03),
    (9, "outward", 0x33), (10, "scrolling", 0x3B), (11, "rolling", 0x37), (12, "rotating", 0x37),
    (13, "explode", 0x33), (14, "launch", 0x33), (15, "ripples", 0x33), (16, "flowing", 0x37),
    (17, "pulsating", 0x33), (18, "tilt", 0x37), (19, "shuttle", 0x33), (0, "off", 0x00),
]


def read_xml(path):
    """The vendor files are not always valid XML (a bare & in config.xml); fix that first"""
    text = open(path, encoding="utf-8-sig").read()
    text = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+);)", "&amp;", text)
    return ET.fromstring(text)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--id", required=True)
    p.add_argument("--vendor", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--notes")
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("config")
    p.add_argument("layout")
    a = p.parse_args()

    cfg = read_xml(a.config)
    usb = []
    for mode in cfg.iter("mode"):
        link = "wired" if mode.get("desc", "").upper().startswith("USB") else "dongle"
        entry = {"vid": mode.get("vid").lower(), "pid": mode.get("pid").lower(), "link": link}
        # The dongle borrows an Apple id (05ac:024f); facet-keyboard never writes to it, so no
        # udev rule for it (it would cover a real Apple keyboard as well)
        if link == "dongle" and entry["vid"] == "05ac":
            entry["udev"] = False
        usb.append(entry)
    kb = cfg.find(".//keyboard")
    if not usb and kb is not None and kb.get("vid"):
        usb.append({"vid": kb.get("vid").lower(), "pid": kb.get("pid").lower(), "link": "wired"})

    lay = read_xml(a.layout)
    light = lay.find("light")
    delay = lay.find("cmd_delaytime")
    keys, keymap, rects = {}, {}, []
    for k in lay.iter("key"):
        code = int(k.get("code"), 16)
        name = HID_ID.get(code)
        if name is None:
            print(f"warning: key {k.get('name')} with code {code:#x} left out", file=sys.stderr)
            continue
        keys[name] = int(k.get("light_index"))
        keymap[name] = int(k.get("key_index"))
        x, y, w, h = (int(k.get(f)) for f in ("rect_left", "rect_top", "rect_width", "rect_height"))
        rects.append((0, name, 0, (x, y, x + w, y + h), None))
    board = {
        "id": a.id, "name": a.name, "vendor": a.vendor, "aliases": a.alias, "family": "sonix-sn32",
        "tested": False, "source": a.source, "notes": a.notes or f"docs/boards/{a.id}.md", "usb": usb,
        "can": {"effects": True, "brightness": int(light.get("brightness_max", 5)),
                "speed": int(light.get("speed_max", 5)), "perkey": light.get("rgb_keyboard") == "1",
                "live": light.get("rgb_keyboard") == "1", "fn": False, "layers": []},
        "protocol": {"delay_ms": int(delay.get("value")) if delay is not None else 35,
                     "rgb": light.get("rgb_keyboard") == "1"},
        "effects": [[v, n, bool(f & 0x02), bool(f & 0x10), bool(f & 0x0C)] for v, n, f in EFFECTS],
        "keys": dict(sorted(keys.items(), key=lambda kv: kv[1])),
        "keymap": dict(sorted(keymap.items(), key=lambda kv: kv[1])),
        "layout": layout_rows(rects),
    }
    print(dump(board))


if __name__ == "__main__":
    main()
