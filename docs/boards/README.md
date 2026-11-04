# Boards

`frame-keyboard` knows a keyboard through one file in
`keyboard/.local/share/frameshell/keyboard/boards/<id>.json` and the code of its family in
`.../keyboard/families/<family>.py`. Boards of one family speak the same protocol; the file
says what is different: ids, keys, effects, layout.

| Family | Code | Boards | Notes |
|---|---|---|---|
| `sinowealth-g5kb` | `families/sinowealth_g5kb.py` | AULA F75, F75 KR, F65, F65 Pro, F99, F99 Pro, AU75, FIRE-75, Womier L65 | [aula-f75.md](aula-f75.md) (protocol), [sinowealth-g5kb.md](sinowealth-g5kb.md) |
| `sonix-sn32` | `families/sonix_sn32.py` | AULA F75 Max | [aula-f75-max.md](aula-f75-max.md) |

No board has been tested with facet yet; `"tested": true` goes into a file once someone has
gone through the test order of its notes on the real board.

## A board file

```jsonc
{
  "id": "aula-f65",                 // file name, what `facet keyboard set` takes
  "name": "AULA F65", "vendor": "AULA", "aliases": [],
  "family": "sinowealth-g5kb",
  "tested": false,
  "source": "where the data comes from (installer, file, version)",
  "notes": "docs/boards/….md",
  "usb": [{"vid": "258a", "pid": "010c", "link": "wired"},
          {"vid": "3554", "pid": "fa09", "link": "dongle"}],
  "can": {"effects": true, "brightness": 4, "speed": 4, "perkey": true, "live": true,
          "fn": true, "layers": ["base", "fn1", "fn2", "tap"]},
  "protocol": {…},                  // per family: device ids, matrix size, delays …
  "effects": [[…], …],              // per family
  "keys": {"esc": 1, …},            // key id -> slot or LED index
  "layout": [[["esc", 1], ["", 0.5], ["f1", 1], …], …],   // rows for the app, "" = gap
  "fn_defaults": {"f1": "0x02000223", …}                   // factory Fn layer, if known
}
```

Key ids are xkb names in lower case, the way the hotkey overlay names keys (`q`, `return`,
`super_l`, `prior`, `kp_7` …), always for the physical key of a US board. A `usb` entry with
`"udev": false` gets no udev rule and does not count when looking for plugged-in boards (for ids
a board borrows from another vendor, like the F75 Max dongle's Apple id).

The `can` flags decide what `frame-keyboard` and the app offer: `effects` built-in effects,
`perkey` colors kept on the board, `live` live frames (`highlight`, the hotkey hints), `fn` the
Fn layer with the `layers` listed.

## Adding a board

1. Find the vendor's configuration app on the vendor's own download page. Unpack it (innoextract,
   7z), never run it.
2. If it is one of the apps above, the converter makes the file: `tools/kbini2board.py` for a
   BYCOMBO4 `KB.ini`, `tools/ddxml2board.py` for a DeviceDriver `layouts/*.xml`. Look at the
   map in the app afterwards; the vendor pictures are not always to scale.
3. Otherwise it needs a new family: read the app (Ghidra for native code, the `.asar` for
   Electron), write the packets down in a `docs/boards/<board>.md` with where each one comes
   from, and keep everything a dry run until it was seen on the board.
4. Check the file: `frame-keyboard keys --board <id>`, `frame-keyboard effect static --board <id>`
   (prints the packets), `frame-keyboard board <id> --json`.

## Asking for a board

Open an issue with the "Keyboard support" form on the
[facet board](https://github.com/orgs/facet-shell/projects/1) (`facet keyboard request` says the
same): the model, `lsusb` with the keyboard plugged in (cable and dongle), and a link to the
vendor's Windows app.

## Looked at, not added

- **Works elsewhere already.** QMK/VIA boards (Keychron Q/V/K Pro, many MonsGeek and Akko
  "MOD" boards) are configured with VIA or Vial, and OpenRGB drives the lighting of the Keychron
  K3 V2 (SONiX, `05ac:024f`), Royuan boards at `3151:xxxx` (Akko 3068B Plus, Epomaker TH80
  Pro, Attack Shark K86), EVision boards (`0c45:5004` …, `320f:50xx`) and Redragon boards on
  `258a:010c` (K686 Eisa Pro). facet does not wrap those yet.
- **AULA F87 (wired, `1a2c:6a80`).** Its DeviceDriver build (1.0.3.2, 2023) talks through its own
  filter driver (`vhidflt.sys`, `DeviceIoControl`) and 64-byte writes; not analysed further.
- **The driveall web driver** (AULA F87 Pro installer, Electron): a newer SONiX protocol for many
  brands, see the end of [aula-f75-max.md](aula-f75-max.md).
