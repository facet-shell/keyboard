# AULA F75 Max (sonix-sn32)

The F75 Max is not an F75 inside: it has a SONiX SN32 chip at USB `0c45:800a` on the cable and
a dongle at `05ac:024f`, and AULA ships another app for it. Board file `boards/aula-f75-max.json`,
code `families/sonix_sn32.py`. **Untested, and worked out from the app alone: nothing here has
been seen on the wire.** Every command is a dry run unless `--live` is given.

What works in frame-keyboard: built-in effects (`effect`), per-key colors kept on the board
(`colors`, `style`), live frames (`highlight`, the hotkey hints) and a backup of the per-key
colors. Not there: the Fn layer (see below) and anything over the dongle.

## Source

`AULA F75 Max Gasket Mechanical Keyboard Driver-1.0.0.5.exe` from
[aulastar.com/keyboard](https://www.aulastar.com/keyboard/) (9 211 944 B, SHA-256 starts
`d99d8cc95f9e453b`), Inno Setup 6.1.0, unpacked with innoextract and never run. Inside:
`DeviceDriver.exe` "Beta 1.0.0.5" (1 802 752 B, 32-bit MFC, hidapi linked in, `© 2024 EPOMAKER x AULA`),
`config.xml` (USB ids), `layouts/rgb-keyboard.xml` (keys) and the language files (effect names).
Decompiled with Ghidra 12.1.4 headless. Addresses below are in that build (image base 0x400000).
The REA MCP server was not reachable in this session, so the analysis is Ghidra output read by hand.

The board file comes from the XML with `tools/ddxml2board.py`.

## Connection

The app opens every HID collection of the board and sorts them by report lengths
(0x0044fa50): a 65-byte feature report is the configuration channel (wired), 33-byte input and
output reports the dongle, usage page 0xFFFF/1 an input channel, a 0x1001-byte output report the
screen. On Linux `frame-keyboard` takes the hidraw node of `0c45:800a` whose report descriptor has
a 64-byte feature report (`95 40` … `b1`): **guess**, the descriptor has not been seen.

Every packet is a feature report without report ID: byte 0 is 0, then 64 data bytes
(hidapi `hid_send_feature_report`, 0x00451430). The app sleeps `cmd_delaytime` (35 ms, from the
XML) before each packet and, for most header packets, reads a feature report back (0x0044eeb0).
There is no checksum on the cable.

Every write has the same shape:

```
04 18                      start ("customization on")
04 <cmd> 00 00 00 00 00 00 <packets>
<packets> x 64 data bytes
04 02                      end
04 F0                      apply
```

The command bytes match OpenRGB's Keychron controller (`KeychronKeyboardController`, Sonix boards
at `05ac:024f`): `02` end, `13` LED effect area, `18`/`19` customization on/off, `F0` effect start.
The F75 Max's own dongle uses that same Apple id.

| Command | Packets | What | Where in the app |
|---|---|---|---|
| `04 13` | 1 | effect page (below) | 0x0042b0d0, 0x00434090, 0x0044b8f0 |
| `04 20` | 8 | live per-key colors, 128 × `[LED, R, G, B]` in key order | 0x00427dc0 (custom light), 0x004329c0 (music) |
| `04 23` | 9 | per-key colors kept on the board, 576 bytes | 0x0044ba00 |
| `04 F5` | 9 | read those back: reply byte 3 = 01, then 9 × 64 bytes | 0x0042aeb0 |
| `04 11` / `04 27` | 9 | key map base / Fn layer, 144 × 4 bytes | 0x004183d0 |
| `04 15`, `04 19` | | macros | 0x0042d6d0 |
| `04 17` | 1 | settings (sleep, debounce …), not decoded | 0x00414020 |
| `04 28` | 1 | not decoded | 0x004238f0 |
| `04 72` | | screen picture (0x1001-byte reports) | 0x00422930 |

### Effect page (`04 13`)

```
0  mode   1-3  R G B   8  colorful (1 = multicolor)   9  brightness   10  speed   11  direction
14-15  AA 55
```

Modes from the app's default table (0x0042b380), names from string ids 521–540:

| Value | Name | Speed | Color | Direction |
|---|---|---|---|---|
| 1 | static | – | yes | – |
| 2, 3 | single-on, single-off | yes | yes | – |
| 4, 5 | glittering, falling | yes | yes | – |
| 6 | colourful | yes | – | – |
| 7 | breathing | yes | yes | – |
| 8 | spectrum | yes | – | – |
| 9 | outward | yes | yes | – |
| 10, 11, 12 | scrolling, rolling, rotating | yes | yes | yes |
| 13, 14, 15 | explode, launch, ripples | yes | yes | – |
| 16 | flowing | yes | yes | yes |
| 17 | pulsating | yes | yes | – |
| 18 | tilt | yes | yes | yes |
| 19 | shuttle | yes | yes | – |
| 0 | off | – | – | – |
| 0x80 | per-key colors (`04 23`) | | | |

The flag column is the app's `config_func` (bit 1 speed, bit 4 color, bits 2/3 direction).
Brightness and speed go to 5 (`brightness_max`/`speed_max` in the XML). The direction values
are a **guess** (Keychron: 0 left→right, 1 right→left, 2 down→up, 3 up→down); `frame-keyboard`
always sends 0.

### Per-key colors

`04 23`: at `LED × 4` the bytes `LED, R, G, B`; `AA 55` at 0x23E. Before that the app selects
mode 0x80 with an effect page that only carries the brightness in byte 9 (0x0044b8f0). The LED
indexes are the `light_index` values of the XML (Esc 1, F1 2 … right arrow 102; row × 18 + column).

`04 20` (live): the same 4-byte records, packed one after the other in key order, 512 bytes, no
end mark, then `04 02`. The app sends it again whenever a color changes while the custom-light tab
is open and for music mode. How long the board keeps such a frame is not known; `highlight`
repeats it every 0.5 s and ends with `04 F0` (**guess**: that starts the saved effect again).

### Key map (not used)

`04 11` (base) or `04 27` (Fn layer), then 576 bytes: 144 entries of 4 bytes at `key_index × 4`,
`AA 55` at 0x23E. Entries (0x004183d0): `02 mods usage 00` a key, `03 usage 00 00` media
(CD play, B7 stop, B6/B5 prev/next, E9/EA volume, E2 mute), `05 02 …` lighting, `06 n …` macro,
`07 …` and `01 …` mouse. The app always writes a whole layer from its own database and has no
read-back for it, so a write from Linux would replace the factory Fn layer, which is not in the
installer. That is why `fn` is off for this board.

## Open questions

- Which hidraw node it is (the feature-report guess above), and whether the read-backs the app
  makes after header packets carry a status the board insists on being read.
- Whether `04 F5` really returns the colors `04 23` stored (the app uses it for the "user light"
  tab).
- Direction values, and whether `04 F0` after live frames brings the effect back.
- The factory Fn layer, for a safe `fn`.
- The dongle protocol (33-byte output reports, checksum in the last byte, 0x0044f5d0).

## Same platform, other app

AULA's newer F87 Pro installer (`F87Pro Software.exe`, NSIS + Electron, `config.driveall.cn`) is a
web driver for many SONiX boards: it lists `0c45:80xx` ids for AULA, LEOBOG, Womier, Epomaker,
GANSS, Cidoo, Varo, Royal Kludge and others (usage page 0xFF68, usage 0x61), and speaks another
framing: 32/64-byte output reports `AA cmd len off_lo off_hi …`, data from byte 8, with the
board descriptions fetched from `config.driveall.cn/json/<brand>/<model>.json`. The F75 Max
(`0c45:800a`) is in its list too. Not implemented; it is the next family to look at.
