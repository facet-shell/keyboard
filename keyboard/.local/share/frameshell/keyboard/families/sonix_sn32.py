"""sonix-sn32: SONiX SN32F2xx boards run by the "DeviceDriver.exe" vendor app: AULA F75 Max
(0c45:800a) for now. The command set is the one OpenRGB's Keychron controller speaks (header
0x04, then a command byte); the boards differ in their key and LED indexes (board file).

Wire format (from DeviceDriver.exe Beta 1.0.0.5 of the F75 Max, docs/boards/aula-f75-max.md):
65-byte feature reports without report ID (byte 0 is 0, then 64 data bytes) on the HID
collection whose feature report is 64 bytes long. A write is a header packet, then the data
in 64-byte packets, then 04 02 (end); 04 F0 makes the board apply it. The app waits
cmd_delaytime (35 ms) between packets and reads a feature report back after the header ones.

Not here on purpose: the key map (04 11 / 04 27): the board has no read-back for it the app
uses, so a write would replace the whole factory Fn layer. The 2.4 GHz dongle (05ac:024f)
speaks a different, checksummed 33-byte protocol and is only listed.
"""
import time
from pathlib import Path

from kblib import Fail, Hidraw, hidraw_nodes, save_backup, key_id

REPORT_LEN = 65
CMD_END = 0x02            # end of a transfer
CMD_START = 0x18          # "customization on", before every write
CMD_EFFECT = 0x13         # LED effect page: one 16-byte page per packet
CMD_LIVE = 0x20           # live per-key colors (the app's "custom light" and music mode)
CMD_PERKEY = 0x23         # per-key colors kept on the board ("user light"), with mode 0x80
CMD_READ_PERKEY = 0xF5    # read those back
CMD_APPLY = 0xF0
CUSTOM_MODE = 0x80        # effect value that shows the per-key colors
PERKEY_BYTES = 576        # 144 x [LED index, R, G, B], 5A A5 ... AA 55 at 0x23E
LIVE_BYTES = 512          # 128 x [LED index, R, G, B], in key order
MARK = b"\xaa\x55"


class Driver:
    family = "sonix-sn32"
    frame_interval = 0.5      # guess: how long the board keeps a live frame is not known

    def __init__(self, board, boards, live, node=None, verbose=False, force_model=False):
        self.boards, self.live = boards, live
        self.use(board)
        if not live:
            self.dev = Hidraw(False, None, verbose)
            return
        nodes = config_nodes(board)
        if node:
            if Path(node).resolve().as_posix() not in [n for n, *_ in nodes]:
                raise Fail(f"{node} is not the configuration interface of a {board.name} (frame-keyboard list)")
        else:
            if not nodes:
                raise Fail(f"no {board.name} with its configuration interface found (frame-keyboard list); "
                           "over the 2.4 GHz dongle this version sends nothing, plug the cable in")
            if len(nodes) > 1:
                raise Fail("more than one candidate: " + ", ".join(n for n, *_ in nodes) + " (pick one with --hidraw)")
            node = nodes[0][0]
        self.dev = Hidraw(True, node, verbose)

    def use(self, board):
        self.board = board
        self.slots = dict(board.keys)                 # key id -> LED index
        self.keymap = dict(board.data.get("keymap", {}))
        self.slot_name = {s: n for n, s in self.slots.items()}
        self.delay = int(board.protocol.get("delay_ms", 35)) / 1000
        self.effects = [tuple(e) for e in board.data.get("effects", [])]
        self.effect_by_name = {e[1]: e for e in self.effects}
        self.brightness_max = int(board.can.get("brightness", 5))
        self.speed_max = int(board.can.get("speed", 5))

    # ------------------------------------------------------------ wire

    @staticmethod
    def title(buf):
        return f"SET_FEATURE report 00 (no ID), 64 bytes, {buf[1]:02x} {buf[2]:02x}"

    def send(self, what, data, ack=False):
        """One packet: report ID 0 and 64 data bytes. ack: read a feature report back, as the
        app does after header packets"""
        buf = bytearray(REPORT_LEN)
        buf[1:1 + len(data)] = data
        self.dev.show(what, buf, 1, 64, self.title(buf))
        if not self.live:
            return None
        time.sleep(self.delay)
        self.dev.set_feature(buf)
        if ack:
            time.sleep(self.delay)
            return self.dev.get_feature(0, REPORT_LEN)
        return None

    def header(self, cmd, packets=0):
        h = bytearray(64)
        h[0], h[1], h[8] = 0x04, cmd, packets
        return h

    def bulk(self, what, data, ack):
        for n in range(0, len(data), 64):
            self.send(f"{what} ({n // 64 + 1}/{len(data) // 64})", data[n:n + 64], ack)

    def transfer(self, what, cmd, data, ack=True, apply=True):
        """04 18, header with the packet count, the data, 04 02 and (apply) 04 F0"""
        self.send("start", self.header(CMD_START), ack=True)
        self.send(f"{what}: header", self.header(cmd, len(data) // 64), ack=True)
        self.bulk(what, data, ack)
        self.send("end", self.header(CMD_END), ack=True)
        if apply:
            self.send("apply", self.header(CMD_APPLY), ack=ack)

    def read_perkey(self):
        """04 F5 with 9 packets: the reply says 01 in byte 3, then 9 x 64 bytes follow"""
        if not self.live:
            print("[dry-run] per-key colors: read skipped (04 f5), using a placeholder")
            return self.perkey_block({})
        reply = self.send("read per-key colors: request", self.header(CMD_READ_PERKEY, 9), ack=True)
        if not reply or reply[4] != 0x01:
            raise Fail("the board did not answer the per-key read (04 f5); not touching it")
        out = bytearray()
        for _ in range(9):
            time.sleep(0.003)
            out += self.dev.get_feature(0, REPORT_LEN)[1:65]
        self.send("end", self.header(CMD_END), ack=True)
        return bytes(out)

    # ------------------------------------------------------------ blocks

    def perkey_block(self, colors, start=None):
        """{LED index: rgb} -> 576 bytes of [index, R, G, B] at index * 4, AA 55 at the end;
        keys not in colors keep what start has"""
        data = bytearray(start if start is not None else PERKEY_BYTES)
        for name, i in self.slots.items():
            data[i * 4] = i
            if i in colors:
                data[i * 4 + 1:i * 4 + 4] = bytes(colors[i])
        data[PERKEY_BYTES - 2:] = MARK
        return data

    def effect_page(self, mode, rgb=(0, 0, 0), colorful=0, brightness=0, speed=0, direction=0):
        page = bytearray(64)
        page[0] = mode
        page[1:4] = bytes(rgb)
        page[8], page[9], page[10], page[11] = colorful, brightness, speed, direction
        page[14:16] = MARK
        return page

    def write_effect(self, page):
        # The app sends the page without reading back, the rest with
        self.send("start", self.header(CMD_START), ack=True)
        self.send("effect: header", self.header(CMD_EFFECT, 1), ack=True)
        self.send("effect page", page)
        self.send("end", self.header(CMD_END), ack=True)
        self.send("apply", self.header(CMD_APPLY))

    def slot_of(self, name, layout):
        key = key_id(name, layout)
        if key not in self.slots:
            raise Fail(f"unknown key '{name}' on the {self.board.name} (frame-keyboard keys)")
        return self.slots[key]

    # ------------------------------------------------------------ for front ends

    def effects_json(self):
        return [{"name": n, "speed": s, "color": c, "direction": d} for _, n, s, c, d in self.effects]

    def actions_json(self):
        raise Fail(f"the {self.board.name} has no Fn layer in frame-keyboard (yet)")

    # ------------------------------------------------------------ commands

    def info(self, a):
        planes = self.read_perkey()
        colors = {k: "#" + planes[i * 4 + 1:i * 4 + 4].hex() for k, i in self.slots.items()}
        if a.json:
            return {"dry": not self.live, "board": self.board.id, "model": self.board.name, "id": "",
                    "node": self.dev.node, "effect": None, "firmwareEffect": None, "effects": {},
                    "sleep": None, "layer": a.layer, "keys": {}, "colors": colors}
        print(f"device: {self.board.name}, {self.dev.node or 'no node (dry run)'}")
        print("effect: the board has no read-back for it in the vendor app")
        for k, c in colors.items():
            print(f"  {k:13} {c}")

    def backup_all(self, a):
        save_backup("perkey", self.read_perkey(), self.live, self.board.id)

    def restore(self, a):
        path = Path(a.file)
        if not path.name.endswith("-perkey.bin"):
            raise Fail("restore takes a ...-perkey.bin from `frame-keyboard backup` on this board")
        data = path.read_bytes()
        if len(data) < PERKEY_BYTES - 2:
            raise Fail("wrong size for per-key colors of this board")
        block = bytearray(data[:PERKEY_BYTES].ljust(PERKEY_BYTES, b"\0"))
        block[PERKEY_BYTES - 2:] = MARK
        self.write_effect(self.effect_page(CUSTOM_MODE, brightness=self.brightness_max))
        self.transfer("per-key colors (restore)", CMD_PERKEY, block)

    def effect(self, a, theme, parse_color):
        if a.name not in self.effect_by_name:
            raise Fail(f"unknown effect '{a.name}' (frame-keyboard effects)")
        value, name, has_speed, has_color, has_direction = self.effect_by_name[a.name]
        bri = self.brightness_max if a.brightness is None else a.brightness
        speed = 3 if a.speed is None else a.speed
        if not 0 <= bri <= self.brightness_max:
            raise Fail(f"brightness goes from 0 to {self.brightness_max} on the {self.board.name}")
        if not 0 <= speed <= self.speed_max:
            raise Fail(f"speed goes from 0 to {self.speed_max} on the {self.board.name}")
        if a.speed is not None and not has_speed:
            print(f"note: {name} has no speed setting")
        rgb, colorful = (255, 255, 255), 0
        if a.color is not None:
            if not has_color:
                raise Fail(f"{name} has no color setting")
            if a.color.lower() == "multi":
                colorful = 1
            else:
                rgb = parse_color(a.color, theme)
        # The app keeps the last settings per effect in its own database; without a read-back
        # this sends what is given and the defaults above for the rest
        self.write_effect(self.effect_page(value, rgb, colorful, bri, speed, 0))

    def perkey(self, a, colors):
        old = self.read_perkey()
        save_backup("perkey", old, self.live, self.board.id)
        bri = self.brightness_max if a.brightness is None else a.brightness
        if not 0 <= bri <= self.brightness_max:
            raise Fail(f"brightness goes from 0 to {self.brightness_max} on the {self.board.name}")
        if getattr(a, "keep_effect", False):
            print("note: the board has no read-back for its effect; --keep-effect writes the colors "
                  "without switching to them")
        else:
            # Mode 0x80 shows the per-key colors (FUN_0044b8f0 in the app)
            self.write_effect(self.effect_page(CUSTOM_MODE, brightness=bri))
        self.transfer("per-key colors", CMD_PERKEY, self.perkey_block(colors, old))

    def frame(self, colors, base):
        # [LED index, R, G, B] for every key, in key order (FUN_00427dc0)
        data = bytearray(LIVE_BYTES)
        for n, (name, i) in enumerate(sorted(self.slots.items(), key=lambda kv: kv[1])[:128]):
            data[n * 4] = i
            data[n * 4 + 1:n * 4 + 4] = bytes(colors.get(i, base))
        self.send("live frame: header", self.header(CMD_LIVE, LIVE_BYTES // 64))
        self.bulk("live frame", data, ack=False)
        self.send("live frame: end", self.header(CMD_END))

    def frame_stop(self):
        # guess: 04 F0 starts the saved effect again
        self.send("live frame: stop (apply the saved effect)", self.header(CMD_APPLY))


def config_nodes(board):
    """hidraw nodes of the wired USB id with a 64-byte feature report (report count 0x40 and a
    Feature item in the descriptor); a guess until it is seen on the board"""
    wired = {(v, p) for v, p, link in board.usb if link == "wired"}
    return [n for n in hidraw_nodes(wired) if is_config(n[4], "wired")]


def is_config(desc, link):
    return link == "wired" and b"\x95\x40" in desc and b"\xb1" in desc
