"""sinowealth-g5kb: SinoWealth SH68F90 boards run by the BYCOMBO4 vendor app (OemDrv.exe,
class CDevG5KB, KB.ini Fw=24): AULA F75, F65, F99, AU75, FIRE-75, Womier L65 ...

All of them sit at USB 258a:010c on the cable and tell themselves apart by the device id
command 0x82 answers (KB.ini "Psd"). The wire format is the same for all: 520-byte feature
reports with report ID 6 on the vendor collection (usage page 0xFF00). What differs per board
(ids, matrix size, keys, effects, factory Fn layer) comes from its board file. The protocol and
where each byte comes from: docs/boards/aula-f75.md.

Before each write in live mode the current block is read back, checked and saved, so
`restore` can put it back. The 2.4 GHz dongle (3554:fa09) is only listed, never written.
"""
import re
from pathlib import Path

from kblib import (Fail, Hidraw, hidraw_nodes, descriptor_has, save_backup, key_id, HID, HID_NAME,
                   MODS, MEDIA, parse_combo)

REPORT_ID = 0x06
REPORT_LEN = 520          # report ID + 7 header bytes + 512 data bytes
CHUNK = 512

# Commands on report 6; a read is the same number | 0x80
CMD_KEYS = 0x03           # key layer, INDEX = layer, MatrixLen * 4 bytes
CMD_CONFIG = 0x04         # 128-byte lighting/system config, ends in 5A A5
CMD_PERKEY = 0x06         # "Self-define" per-key colors: planes R, G, B of 126 bytes
CMD_FRAME = 0x08          # live frame in RAM, 126 x RGB; INDEX 2 + 00 00 00 ends it
CMD_COLORTAB = 0x0A       # color table: a 21-byte row (7 RGB slots) per effect, 5A A5 at 0x1FA
CMD_DEVICE_ID = 0x82      # read only, 6 bytes: KB.ini "Psd"
PLANE = 126               # per-key plane stride the app uses (0x7e)
FRAME_BYTES = 126 * 3     # 0x17a, interleaved R G B per slot
CONFIG_BYTES = 128
COLORTAB_BYTES = 512
MARK = b"\x5a\xa5"
LAYER_END = b"\x00\x00" + MARK  # last entry of every layer when KB.ini says CRC=1
LAYERS = {"base": 0, "fn1": 1, "fn2": 2, "tap": 3}
MULTI_SLOT = 7            # color slot 7 = "ColorFull" (RandomVal in the app)

# Fn-layer entries are 4 bytes [type, mods, key2, key1]; type 00 key, 02 consumer, 08 lighting
LIGHT = {"light-up": (0x08, 0x03, 0x01, 0x00), "light-down": (0x08, 0x03, 0x02, 0x00),
         "speed-up": (0x08, 0x04, 0x01, 0x00), "speed-down": (0x08, 0x04, 0x02, 0x00),
         "effect-next": (0x08, 0x02, 0x00, 0x00)}


def raw_entry(text):
    v = int(text, 16)
    return (v >> 24, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


class Driver:
    """facet-keyboard's commands for one board of the family"""

    family = "sinowealth-g5kb"

    def __init__(self, board, boards, live, node=None, verbose=False, force_model=False):
        self.boards = boards
        self.live, self.force_model = live, force_model
        self.use(board)
        if not live:
            self.dev = Hidraw(False, None, verbose)
            self.read("device id", CMD_DEVICE_ID, 6, index=1, placeholder=next(iter(self.ids), bytes(6)))
            return
        nodes = config_nodes(board)
        if node:
            # A node given by hand still has to be the configuration interface of such a board
            if Path(node).resolve().as_posix() not in [n for n, *_ in nodes]:
                raise Fail(f"{node} is not the configuration interface of a {board.name} (facet-keyboard list)")
        else:
            if not nodes:
                dongle = [(v, p) for v, p, link in board.usb if link == "dongle"]
                if dongle and hidraw_nodes(set(dongle)):
                    raise Fail("only the 2.4 GHz dongle is there; this version talks to the keyboard "
                               "over the USB cable. Plug the cable in.")
                raise Fail(f"no {board.name} with its configuration interface found (facet-keyboard list)")
            if len(nodes) > 1:
                raise Fail("more than one candidate: " + ", ".join(n for n, *_ in nodes) + " (pick one with --hidraw)")
            node = nodes[0][0]
        self.dev = Hidraw(True, node, verbose)
        self.identify()

    def use(self, board):
        self.board = board
        p = board.protocol
        self.ids = {bytes(int(x, 16) for x in k.split()): v for k, v in p.get("ids", {}).items()}
        self.matrix_len = int(p.get("matrix_len", 128))
        self.layer_bytes = self.matrix_len * 4
        self.perkey_bytes = self.matrix_len * 3   # three planes of 126 and the rest spare
        self.slots = dict(board.keys)
        self.slot_name = {s: n for n, s in self.slots.items()}
        self.effects = [tuple(e) for e in board.data.get("effects", [])]
        self.effect_by_name = {e[2]: e for e in self.effects}
        self.effect_by_fw = {e[1]: e for e in self.effects}
        self.self_define = self.effect_by_name.get("self-define")
        self.fn_slot = self.slots.get("fn")
        self.fn_defaults = {k: raw_entry(v) for k, v in board.data.get("fn_defaults", {}).items()}
        self.brightness_max = int(board.can.get("brightness", 4))
        self.speed_max = int(board.can.get("speed", 4))
        self.brightness_hw = p.get("brightness_hw")
        self.speed_hw = p.get("speed_hw")
        # Self-define brightness: the app writes it at the effect's position like every
        # other effect; the L65 firmware reads it at 0x75 (verified there)
        self.sd_bright = p.get("selfdefine_brightness")
        # The only writes this tool makes, (command, index) -> size. Never 0x05 (macros),
        # 0x11 (reset) or report 5 (bootloader).
        self.write_sizes = {
            **{(CMD_KEYS, layer): self.layer_bytes for layer in range(4)},
            (CMD_CONFIG, 0): CONFIG_BYTES, (CMD_PERKEY, 0): self.perkey_bytes,
            (CMD_COLORTAB, 0): COLORTAB_BYTES, (CMD_FRAME, 0): FRAME_BYTES, (CMD_FRAME, 2): 3,
        }
        self.readable = {CMD_KEYS | 0x80, CMD_CONFIG | 0x80, CMD_PERKEY | 0x80, CMD_COLORTAB | 0x80, CMD_DEVICE_ID}

    # ------------------------------------------------------------ wire

    @staticmethod
    def packet(cmd, index, size, data=b"", npkt=1, pkt=0):
        buf = bytearray(REPORT_LEN)
        buf[0:8] = bytes([REPORT_ID, cmd, index, 0, npkt, pkt, size & 0xFF, size >> 8])
        buf[8:8 + len(data)] = data
        return buf

    @staticmethod
    def title(buf):
        return (f"SET_FEATURE report {buf[0]:02x} cmd {buf[1]:02x} index {buf[2]:02x} "
                f"packet {buf[5] + 1}/{buf[4]} size {buf[6] | buf[7] << 8}")

    def write(self, what, cmd, data, index=0, delay=0.0):
        import time
        if (cmd, index) not in self.write_sizes:
            raise Fail(f"refusing to write command {cmd:#04x} index {index}")
        if self.write_sizes[(cmd, index)] != len(data):
            raise Fail(f"refusing to write {len(data)} bytes with command {cmd:#04x} index {index}")
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        for n, chunk in enumerate(chunks):
            buf = self.packet(cmd, index, len(chunk), chunk, len(chunks), n)
            self.dev.show(what, buf, 8, len(chunk), self.title(buf))
            if self.live:
                time.sleep(delay)
                self.dev.set_feature(buf)   # like the app: 3 tries, 70 ms apart, 20 ms before each

    def read(self, what, cmd, size, index=0, placeholder=None):
        """SET_FEATURE with the read command (cmd | 0x80), then GET_FEATURE.
        A dry run returns the placeholder instead."""
        import time
        cmd |= 0x80
        if cmd not in self.readable:
            raise Fail(f"refusing to read with command {cmd:#04x}")
        if not self.live:
            print(f"[dry-run] {what}: read skipped (cmd {cmd:02x}), using a placeholder")
            return bytes(placeholder if placeholder is not None else bytes(size))
        out = bytearray()
        npkt = (size + CHUNK - 1) // CHUNK
        for n in range(npkt):
            part = min(CHUNK, size - n * CHUNK)
            buf = self.packet(cmd, index, part, npkt=npkt, pkt=n)
            self.dev.show(what + " (read request)", buf, 8, 0, self.title(buf))
            self.dev.set_feature(buf)
            time.sleep(0.02)
            out += self.dev.get_feature(REPORT_ID, REPORT_LEN)[8:8 + part]
        return bytes(out)

    # ------------------------------------------------------------ model

    def identify(self):
        """Live only: the device id decides the board. When it belongs to another board of the
        family the tool switches to that board file (they all share 258a:010c)."""
        ident = self.read("device id", CMD_DEVICE_ID, 6, index=1)   # the app asks with index 1, size 6
        self.ident = ident
        if ident in self.ids:
            return
        for b in self.boards.values():
            if b.family == self.family and b is not self.board:
                ids = {bytes(int(x, 16) for x in k.split()) for k in b.protocol.get("ids", {})}
                if ident in ids:
                    print(f"note: the keyboard says it is a {b.name}, not a {self.board.name}; using that "
                          f"board file (keep it with: facet keyboard set {b.id})")
                    self.use(b)
                    return
        if self.force_model:
            print(f"warning: unknown device id {ident.hex(' ')}, going on because of --force-model")
            return
        raise Fail(f"device id {ident.hex(' ')} belongs to no board in the registry; not writing. "
                   "Ask for it on the facet board (facet keyboard request)")

    def model(self):
        if not self.live:
            ident = next(iter(self.ids), bytes(6))
            return self.ids.get(ident, self.board.name), ident
        return self.ids.get(self.ident, "unknown"), self.ident

    # ------------------------------------------------------------ blocks

    def placeholder_config(self):
        cfg = bytearray(CONFIG_BYTES)
        cfg[0x0A] = 11                     # neon stream, the factory effect
        cfg[0x0B] = 0x20
        for pos, *_ in self.effects:
            cfg[0x38 + 2 * pos] = min(4, self.brightness_max)
            cfg[0x39 + 2 * pos] = 2 << 4
        cfg[0x7E:0x80] = MARK
        return cfg

    def placeholder_layer(self, layer_no=0):
        """Stand-in for a dry run: base = every key itself, fn1 = the factory Fn layer"""
        layer = bytearray(self.layer_bytes)
        for name, slot in self.slots.items():
            if slot * 4 + 4 > self.layer_bytes - 4:
                continue
            if layer_no == LAYERS["base"] and name in HID:
                layer[slot * 4 + 3] = HID[name]
            elif layer_no == LAYERS["fn1"] and name in self.fn_defaults:
                layer[slot * 4:slot * 4 + 4] = bytes(self.fn_defaults[name])
        if self.fn_slot is not None:
            layer[self.fn_slot * 4:self.fn_slot * 4 + 4] = bytes([0x0D, 0, 0, 0])
        layer[-4:] = LAYER_END
        return layer

    @staticmethod
    def placeholder_colortab():
        tab = bytearray(COLORTAB_BYTES)
        tab[0x1FA:0x1FC] = MARK
        return tab

    @staticmethod
    def check_mark(block, offset, what):
        if bytes(block[offset:offset + 2]) != MARK:
            raise Fail(f"{what} has no 5A A5 at {offset:#x} (found {bytes(block[offset:offset + 2]).hex()}); "
                       "not touching it")

    def read_config(self):
        cfg = bytearray(self.read("lighting config", CMD_CONFIG, CONFIG_BYTES, placeholder=self.placeholder_config()))
        self.check_mark(cfg, 0x7E, "the config block")
        return cfg

    def read_layer(self, layer):
        data = bytearray(self.read(f"key layer {layer}", CMD_KEYS, self.layer_bytes, index=layer,
                                   placeholder=self.placeholder_layer(layer)))
        if bytes(data[-4:]) != LAYER_END:
            raise Fail(f"key layer {layer} does not end in 00 00 5A A5; not touching it")
        return data

    def read_colortab(self):
        tab = self.read("color table", CMD_COLORTAB, COLORTAB_BYTES, placeholder=self.placeholder_colortab())
        self.check_mark(tab, 0x1FA, "the color table")
        return tab

    def backup(self, kind, data):
        return save_backup(kind, data, self.live, self.board.id)

    def write_config(self, cfg, old):
        self.check_mark(cfg, 0x7E, "the new config")
        self.backup("config", old)
        self.write("lighting config", CMD_CONFIG, cfg, delay=0.06)

    # ------------------------------------------------------------ names and actions

    def slot_of(self, name, layout):
        key = key_id(name, layout)
        if key not in self.slots:
            raise Fail(f"unknown key '{name}' on the {self.board.name} (facet-keyboard keys)")
        return self.slots[key]

    def hw_level(self, value, table, top, what):
        if not 0 <= value <= top:
            raise Fail(f"{what} goes from 0 to {top} on the {self.board.name}")
        return table[value] if table else value

    def parse_action(self, text, layout="us"):
        t = text.strip().lower()
        if t in ("none", "disable"):
            return (0, 0, 0, 0)
        if t in LIGHT:
            return LIGHT[t]
        if t in MEDIA:
            return (0x02, 0x00, MEDIA[t] >> 8, MEDIA[t] & 0xFF)
        if re.fullmatch(r"0x[0-9a-f]{8}", t):          # raw, as KB.ini writes it: 0xTTMMK2K1
            return raw_entry(t)
        combo = parse_combo(t, layout)
        if combo is None:
            raise Fail(f"unknown action '{text}': a key (f5), a combo (ctrl+alt+t), "
                       f"{', '.join(MEDIA)}, {', '.join(LIGHT)}, none, or 0xTTMMK2K1")
        mods, keys = combo
        if not 1 <= len(keys) <= 2:
            raise Fail(f"'{text}': one or two keys, plus modifiers")
        return (0x00, mods, keys[0] if len(keys) == 2 else 0, keys[-1])

    @staticmethod
    def describe_entry(e, layout="us"):
        from kblib import LAYOUT_DE
        typ, mods, k2, k1 = e
        # Name keys the way the chosen layout labels them (HID usages are US positions)
        shown = {v: k for k, v in LAYOUT_DE.items()} if layout == "de" else {}
        if e == (0, 0, 0, 0):
            return "nothing"
        if typ == 0x00:
            names = [n for n, bit in MODS if mods & bit]
            names += [shown.get(HID_NAME.get(k), HID_NAME.get(k, f"{k:#04x}")) for k in (k2, k1) if k]
            return "+".join(names)
        if typ == 0x02:
            u = (k2 << 8) | k1
            return next((n for n, v in MEDIA.items() if v == u), f"consumer {u:#06x}")
        if typ == 0x08:
            return next((n for n, v in LIGHT.items() if v == e), "lighting " + bytes(e).hex(" "))
        if typ == 0x0D:
            return "fn" if mods == 0 else "fn2"
        if typ == 0x07:
            return f"keyboard function {k1:#04x} (mode, channel, lock ...)"
        return "type " + bytes(e).hex(" ")

    # ------------------------------------------------------------ for front ends

    def effects_json(self):
        return [{"name": name, "speed": speed, "color": color} for _, _, name, speed, color in self.effects]

    def actions_json(self):
        return {"media": list(MEDIA), "lighting": list(LIGHT), "mods": [n for n, _ in MODS],
                "keys": list(HID), "defaults": {k: "0x" + bytes(v).hex() for k, v in self.fn_defaults.items()}}

    # ------------------------------------------------------------ commands

    def show_layer(self, layer, label, layout="us"):
        print(f"{label} layer:")
        for name, slot in sorted(self.slots.items(), key=lambda kv: kv[1]):
            e = tuple(layer[slot * 4:slot * 4 + 4])
            if e != (0, 0, 0, 0):
                print(f"  {name:13} {bytes(e).hex(' ')}  {self.describe_entry(e, layout)}")

    def info(self, a):
        name, ident = self.model()
        cfg = self.read_config()
        eff = self.effect_by_fw.get(cfg[0x0A])
        if a.json:
            layer = self.read_layer(LAYERS[a.layer])
            planes = self.read("per-key colors", CMD_PERKEY, self.perkey_bytes)
            keys, colors = {}, {}
            for key, slot in self.slots.items():
                e = tuple(layer[slot * 4:slot * 4 + 4])
                if e != (0, 0, 0, 0):
                    keys[key] = {"bytes": bytes(e).hex(" "), "text": self.describe_entry(e, a.layout)}
                if slot < PLANE:
                    colors[key] = "#" + bytes((planes[slot], planes[PLANE + slot], planes[2 * PLANE + slot])).hex()
            return {
                "dry": not self.live, "board": self.board.id, "model": name, "id": ident.hex(" "),
                "node": self.dev.node, "effect": eff[2] if eff else None, "firmwareEffect": cfg[0x0A],
                "effects": {e[2]: {"brightness": cfg[0x38 + 2 * e[0]], "speed": cfg[0x39 + 2 * e[0]] >> 4,
                                   "slot": cfg[0x39 + 2 * e[0]] & 0x0F} for e in self.effects},
                "sleep": cfg[0x18], "layer": a.layer, "keys": keys, "colors": colors,
            }
        print(f"device: {name}, id {ident.hex(' ')}, {self.dev.node or 'no node (dry run)'}")
        print(f"effect: {eff[2] if eff else '?'} (firmware number {cfg[0x0A]}), self-define flag {cfg[0x09]}")
        if eff:
            sc = cfg[0x39 + 2 * eff[0]]
            print(f"        brightness {cfg[0x38 + 2 * eff[0]]}, speed {sc >> 4}, color slot {sc & 0xF}")
        print(f"debounce byte {cfg[0x03]}, sleep {cfg[0x18]} (half minutes)")
        print("config: " + cfg.hex(" "))
        self.show_layer(self.read_layer(LAYERS[a.layer]), a.layer, a.layout)

    def backup_all(self, a):
        blocks = [("config", self.read_config()), ("colortab", self.read_colortab()),
                  ("perkey", self.read("per-key colors", CMD_PERKEY, self.perkey_bytes))]
        blocks += [(f"layer-{name}", self.read_layer(n)) for name, n in LAYERS.items()]
        for kind, data in blocks:
            self.backup(kind, data)

    def restore(self, a):
        path = Path(a.file)
        m = re.search(r"-(config|colortab|perkey|layer-(base|fn1|fn2|tap))\.bin$", path.name)
        if not m:
            raise Fail("restore takes a file from `facet-keyboard backup` (...-config.bin, ...-layer-fn1.bin ...)")
        data = path.read_bytes()
        kind = m.group(1)
        # Check the file completely before anything goes to the keyboard
        if kind == "config":
            if len(data) != CONFIG_BYTES:
                raise Fail("wrong size for a config block")
            self.check_mark(data, 0x7E, "the backup")
        elif kind == "colortab":
            if len(data) != COLORTAB_BYTES:
                raise Fail("wrong size for a color table")
            self.check_mark(data, 0x1FA, "the backup")
        elif kind == "perkey":
            if len(data) != self.perkey_bytes:
                raise Fail(f"wrong size for per-key colors of the {self.board.name}")
        else:
            if len(data) != self.layer_bytes or data[-4:] != LAYER_END:
                raise Fail(f"the layer backup has the wrong size for the {self.board.name} or no 00 00 5A A5 at the end")
            fn = self.fn_slot
            if m.group(2) == "base" and fn is not None and data[fn * 4:fn * 4 + 4] != b"\x0d\x00\x00\x00":
                raise Fail("this base layer has no Fn on the Fn key; refusing (Fn would be gone)")
        # Like every other write: read and check what is there now and keep a copy of it
        cfg = self.read_config()
        if kind == "config":
            self.write_config(bytearray(data), cfg)
        elif kind == "colortab":
            self.backup("colortab", self.read_colortab())
            self.write("color table (restore)", CMD_COLORTAB, data)
        elif kind == "perkey":
            self.backup("perkey", self.read("per-key colors", CMD_PERKEY, self.perkey_bytes))
            self.write("per-key colors (restore)", CMD_PERKEY, data)
            self.write_config(bytearray(cfg), cfg)
        else:
            layer = m.group(2)
            self.backup(f"layer-{layer}", self.read_layer(LAYERS[layer]))
            self.write(f"key layer {layer} (restore)", CMD_KEYS, data, index=LAYERS[layer])
            self.write_config(bytearray(cfg), cfg)      # commits the layer, as after `fn`

    def effect(self, a, theme, parse_color):
        if a.name not in self.effect_by_name:
            raise Fail(f"unknown effect '{a.name}' (facet-keyboard effects)")
        pos, fw, name, has_speed, has_color = self.effect_by_name[a.name]
        old = self.read_config()
        cfg = bytearray(old)
        # Bytes the app writes on every lighting change (FUN_0048d680)
        cfg[0x09] = 1 if self.self_define and fw == self.self_define[1] else 0
        cfg[0x0A] = fw
        cfg[0x0B] = 0x20                   # StartGp 0x20 + color group 0
        cfg[0x38] = cfg[0x39] = 0xFF
        bri, sc = cfg[0x38 + 2 * pos], cfg[0x39 + 2 * pos]
        speed, slot = sc >> 4, sc & 0x0F
        if a.brightness is not None:
            bri = self.hw_level(a.brightness, self.brightness_hw, self.brightness_max, "brightness")
        if a.speed is not None:
            if not has_speed:
                print(f"note: {name} has no speed setting")
            speed = self.hw_level(a.speed, self.speed_hw, self.speed_max, "speed")
        if a.color is not None:
            if not has_color:
                raise Fail(f"{name} has no color setting")
            c = a.color.lower()
            if c == "multi":
                slot = MULTI_SLOT
            elif re.fullmatch(r"[0-6]", c):
                slot = int(c)
            else:
                rgb = parse_color(a.color, theme)
                tab_old = self.read_colortab()
                tab = bytearray(tab_old)
                row = pos * 21                 # row n belongs to LedOpt position n; slot 0 = bytes R G B
                tab[row:row + 3] = bytes(rgb)
                self.backup("colortab", tab_old)
                self.write("color table", CMD_COLORTAB, tab)
                slot = 0
        cfg[0x38 + 2 * pos] = bri
        cfg[0x39 + 2 * pos] = (speed << 4) | slot
        if fw == (self.self_define or (0, -1))[1] and self.sd_bright is not None and a.brightness is not None:
            cfg[self.sd_bright] = bri
        self.write_config(cfg, old)

    def fn(self, a):
        layer_no = LAYERS[a.layer]
        changes = []
        if a.reset and layer_no != LAYERS["fn1"]:
            raise Fail("--reset knows the factory Fn layer (fn1) only")
        if a.reset and not self.fn_defaults:
            raise Fail(f"the factory Fn layer of the {self.board.name} is not known (no list in its board file)")
        for item in a.items:
            if a.reset:
                slot = self.slot_of(item, a.layout)
                entry = self.fn_defaults.get(self.slot_name[slot], (0, 0, 0, 0))
            else:
                if "=" not in item:
                    raise Fail(f"'{item}': write key=action, e.g. f1=play")
                key, action = item.split("=", 1)
                slot, entry = self.slot_of(key, a.layout), self.parse_action(action, a.action_layout or a.layout)
            if slot == self.fn_slot:
                raise Fail("the Fn key itself stays as it is (otherwise Fn could be gone)")
            if slot == self.slots.get("knob") and not a.allow_knob:
                raise Fail("the knob press is left alone unless --allow-knob is given")
            if slot * 4 + 4 > self.layer_bytes - 4:
                raise Fail(f"slot {slot} is outside the key layer of the {self.board.name}")
            changes.append((item, slot, entry))
        if not changes and not a.show:
            raise Fail("nothing to do: fn <key>=<action> ..., fn --show or fn --reset <key> ...")
        layer_old = self.read_layer(layer_no)
        if a.show:
            self.show_layer(layer_old, a.layer, a.layout)
            return
        cfg = self.read_config()              # checked before anything is written
        layer = bytearray(layer_old)
        if layer_no == LAYERS["base"]:
            print("note: this changes the BASE layer (what the keys do without Fn)")
        for item, slot, entry in changes:
            layer[slot * 4:slot * 4 + 4] = bytes(entry)
            print(f"  {item}: {bytes(entry).hex(' ')} ({self.describe_entry(entry, a.action_layout or a.layout)})")
        if self.fn_slot is not None:
            fn = slice(self.fn_slot * 4, self.fn_slot * 4 + 4)
            if layer[fn] != layer_old[fn]:
                raise Fail("internal error: the Fn key would change; nothing written")
        if bytes(layer[-4:]) != LAYER_END:
            raise Fail("internal error: the layer end mark would change; nothing written")
        self.backup(f"layer-{a.layer}", layer_old)
        self.write(f"key layer {a.layer}", CMD_KEYS, layer, index=layer_no)
        # The app writes the lighting config after the layers; on the L65 that commits them
        self.write_config(bytearray(cfg), cfg)

    def perkey_block(self, colors, start=None):
        """{slot: rgb} -> what the app sends: R plane, G plane, B plane (126 bytes each), spare bytes.
        Keys not in colors keep their color from start."""
        data = bytearray(start if start is not None else self.perkey_bytes)
        for slot, (r, g, b) in colors.items():
            if slot < PLANE:
                data[slot], data[PLANE + slot], data[2 * PLANE + slot] = r, g, b
        return data

    def perkey(self, a, colors):
        """Per-key colors kept on the keyboard (Self-define); keys not named keep their color"""
        if not self.self_define:
            raise Fail(f"the {self.board.name} has no self-define effect")
        old = self.read_config()              # checked before anything is written
        old_keys = self.read("per-key colors", CMD_PERKEY, self.perkey_bytes)
        self.backup("perkey", old_keys)
        self.write("per-key colors (self-define)", CMD_PERKEY, self.perkey_block(colors, old_keys), index=0)
        if getattr(a, "keep_effect", False) and old[0x0A] != self.self_define[1]:
            # The colors wait on the keyboard until Self-define is picked; the effect stays
            print("the keyboard keeps its effect; the colors show once self-define is picked")
            return
        cfg = bytearray(old)
        pos = self.self_define[0]
        cfg[0x09], cfg[0x0A], cfg[0x0B] = 1, self.self_define[1], 0x20
        if a.brightness is not None:
            cfg[0x38 + 2 * pos] = self.hw_level(a.brightness, self.brightness_hw, self.brightness_max, "brightness")
        elif cfg[0x38 + 2 * pos] == 0:
            cfg[0x38 + 2 * pos] = min(4, self.brightness_max)
        if self.sd_bright is not None:
            if a.brightness is not None or cfg[self.sd_bright] == 0:
                cfg[self.sd_bright] = cfg[0x38 + 2 * pos]
        self.write_config(cfg, old)

    # Live frames: what `highlight` sends again and again while keys are lit
    frame_interval = 0.5          # the board drops back to its effect about a second after the last frame

    def frame(self, colors, base):
        data = bytearray(FRAME_BYTES)
        for slot in range(126):
            data[slot * 3:slot * 3 + 3] = bytes(colors.get(slot, base))
        self.write("live frame", CMD_FRAME, data, index=0, delay=0.007)

    def frame_stop(self):
        self.write("live facet: stop", CMD_FRAME, b"\x00\x00\x00", index=2)


def config_nodes(board):
    """hidraw nodes of the board's wired USB ids with the configuration collection: vendor
    page 0xFF00 with report ID 6 (MI_01)"""
    wired = {(v, p) for v, p, link in board.usb if link == "wired"}
    return [n for n in hidraw_nodes(wired) if descriptor_has(n[4], 0xFF00, REPORT_ID)]


def is_config(node_desc, link):
    return link == "wired" and descriptor_has(node_desc, 0xFF00, REPORT_ID)
