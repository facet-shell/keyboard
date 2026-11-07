# sinowealth-g5kb: AULA F75 and its siblings

Boards with a SinoWealth SH68F90 on the cable (USB `258a:010c`) and a Beken BK3632 dongle
(`3554:fa09`), configured by AULA's "BYCOMBO4" app (`OemDrv.exe`, class `CDevG5KB`, `Fw=24`
in the board's `KB.ini`). The protocol is written down in [aula-f75.md](aula-f75.md); this
file is about how the other boards differ and where their board files come from.
Code: `families/sinowealth_g5kb.py`. **None of these boards has been tested with facet.**

## Boards

| Board | Device ids (`Psd`) | Matrix | Brightness | Keys | Knob | Factory Fn entries |
|---|---|---|---|---|---|---|
| AULA F75 | `03 00 00 00 00 cd`, F75 Wired `…00 e1` | 128 | 0–9 | 81 | yes | 36 |
| AULA F75 KR | `03 00 00 00 01 91` | 128 | 0–9 | 81 | yes | 36 |
| AULA F65 | `03 00 00 00 01 aa` | 128 | 0–4 | 67 | – | 39 |
| AULA F65 Pro | `03 00 00 00 03 15` | 128 | 0–4 | 67 | yes | 44 |
| AULA F99 | `03 00 00 00 00 a4`, F99 Wired `…00 9a` | 128 | 0–4 | 100 | – | 30 |
| AULA F99 Pro | `03 00 00 00 01 58` | 128 | 0–4 | 100 | yes | 39 |
| AULA AU75 | `03 00 00 00 00 c6` | 128 | 0–4 | 82 | yes | 40 |
| AULA FIRE-75 | `03 00 00 00 01 15`, Wired `…01 e3` | 128 | 0–9 | 81 | yes | 34 |
| Womier L65 | `03 00 00 00 01 ca` | 126 | 0–4 | 69 | yes | not known |

All of them answer on `258a:010c`, so the USB id alone does not say which one is plugged in.
`facet-keyboard` takes the board you picked (`facet keyboard set`), and with `--live` reads the
device id (command `0x82`) before anything else. An id that belongs to another board file
switches to that file, an id that belongs to none stops the command.

## Where the board files come from

`tools/make-g5kb-boards.sh` turns the `KB.ini` files into board files with
`tools/kbini2board.py`. Sources, all from the Driver links on
[aulastar.com/keyboard](https://www.aulastar.com/keyboard/) (downloaded 2026-11-04, unpacked
with innoextract, never run; the installers are Inno Setup 5.3.3):

| Installer (SHA-256, first 16) | App | `KB.ini` used |
|---|---|---|
| `AULA F75 driver.exe` (f9938e1e36511410) | OemDrv.exe 2 633 728 B, Ver 0x200 | `Dev/kb/1`, `wired`, `F75KR` |
| `AULA F65 driver.exe` (224dffb9bd16a8d8) | OemDrv.exe 2 640 896 B, Ver 0x200 | `Dev/kb` |
| `AULA F65PRO driver.exe` (3b621a2d7a5ccf4b) | OemDrv.exe 2 664 448 B, Ver 0x201 | `Dev/kb/F65PRO` |
| `AULA F99 driver.exe` (3941988682bbbbde) | OemDrv.exe 2 670 592 B, Ver 0x200 | `Dev/kb` (German ISO edition) |
| `AULA F99 Pro driver.exe` (de6b40ebf90232be) | OemDrv.exe 2 636 800 B, Ver 0x200 | `Dev/kb/1` (F99), `F99Pro`, `wired` |
| `AULA AU75 driver.exe` (8be48d49e4f6823e) | OemDrv.exe 2 655 232 B, Ver 0x200 | `Dev/kb/AU75` |
| `AULA FIRE-75 driver.exe` (cb41616a07aec13f) | OemDrv.exe 2 656 768 B, Ver 0x201 | `Dev/kb/FIRE75`, `FIRE75USB` |

The Womier L65 file is built by hand from
[womier-l65-linux](https://github.com/deepan-alve/womier-l65-linux) (`linux/l65ctl.py`,
`docs/L65-protocol.md`), which was verified on an L65. No Womier installer was at hand.

What the converter reads from a `KB.ini`:

- `[OPT]`: `VID`/`PID` (cable), `VID_Wireless`/`PID_Wireless` (dongle), `Psd` (device id),
  `MatrixLen` (entries per key layer), `Light`/`LightHW` and `Speed`/`SpeedHW` (levels),
  `LedOpt1..20` (`hw effect speed light direct random color`: firmware number, name index,
  then flags). The firmware numbers are the same on every board, the names come from the L65.
- `[KEY]`: `K<n>=x1,y1,x2,y2, type, VK, ?, slot`. The VK code names the key, the last field is
  its slot (column × 6 + row), the rectangle is where the app draws it. Keys whose top edges
  line up form a row of the map; widths are rounded to quarter keys. A key without a
  rectangle (the knob, type 0x09 with its action) goes to the end of the top row.
- `[FN1]`: `K<n>=type,…`: type 0x09 is a raw entry `0xTTMMK2K1`, type 0x02 a VK code that
  is turned into a HID usage. These are what `fn --reset` puts back.

## Open questions

- **MatrixLen.** OemDrv reads `MatrixLen` with a default of 128 in all seven builds (checked:
  the `push 0x80` before the `GetPrivateProfileIntW(L"OPT", L"MatrixLen", …)` call). The F65,
  F65 Pro, AU75 and FIRE-75 files have no `MatrixLen`, so the app sends 512-byte key layers to
  them. The L65 takes 504 (126 entries, verified there). Whether the 128 boards really have 128
  entries, or the firmware ignores the last two, nobody has checked. `info --live` shows it: a
  layer that does not end in `00 00 5A A5` is refused, nothing is written.
- **Self-define brightness.** The app writes it at `0x38 + 2 × position` like every other
  effect (0x5E on the F75); the L65 firmware reads it at `0x75` (verified there). The L65 file
  says so (`selfdefine_brightness`), the AULA files follow the app. If per-key colors stay dark
  on an AULA board, that offset is the first thing to look at.
- **ISO keys.** Only the German F99 file has the extra key next to left Shift (`less`, slot 76).
  The other boards are drawn as ANSI; where their ISO `#` key sits is not known.
- **Two Enters, Calc.** The F99 and F99 Pro have VK codes 0xFD (numpad Enter) and 0x99 (a key
  above the numpad, `calc`) that are the app's own; their HID usage on the board is a guess.
- **Brightness levels.** F75 and FIRE-75 list ten (0–9), the others five (0–4). Whether ten
  levels are ten steps on the board is not known.
- **Dongle.** Only listed. The L65 project found that per-key colors and live frames do not get
  through it.
