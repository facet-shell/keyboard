# facet · keyboard

Lighting and keys for keyboards from the shell (Super+K), without the vendor's Windows app.

Part of [facet](https://github.com/facet-shell); install it with the `facet` command:

```bash
facet add keyboard
```

It asks which keyboard you have; a supported board that is plugged in comes first in the list.
Change it later with `facet keyboard set`, see the list with `facet keyboard list`.

## Boards

| Board | Family | State |
|---|---|---|
| AULA F75 (and F75 Wired), F75 KR | sinowealth-g5kb | untested |
| AULA F65, F65 Pro | sinowealth-g5kb | untested |
| AULA F99 (and F99 Wired), F99 Pro | sinowealth-g5kb | untested |
| AULA AU75, FIRE-75 | sinowealth-g5kb | untested |
| Womier L65 | sinowealth-g5kb | untested here, protocol verified by [womier-l65-linux](https://github.com/deepan-alve/womier-l65-linux) |
| AULA F75 Max | sonix-sn32 | untested; effects, per-key colors and live frames, no Fn layer |

Nothing has been sent to a real board from facet yet. Until a board is marked tested, every
command is a dry run: `frame-keyboard` prints the packets it would send and the app shows
placeholder data. `--live` sends. Each board's notes in [docs/boards](docs/boards/README.md)
have the order to test it in and what is still open.

## Using it

```bash
facet keyboard list              # every supported board
facet keyboard search aula 75    # filter by vendor or model
facet keyboard set aula-f75      # or without a name: pick from the list
frame-keyboard list              # what is plugged in (reads sysfs only)
frame-keyboard info --live       # first test on the board
```

Super+K opens the app: lighting, Fn keys and per-key colors, as far as the board can do them.
While modifiers are held, the keys that do something with them light up (boards with live
frames, once the app is live).

## Your keyboard is not listed

Ask for it on [the board](https://github.com/orgs/facet-shell/projects/1) with an issue (the
Idea form, "Keyboard support: <model>"): the model, `lsusb` with the keyboard plugged in (cable and dongle) and a
link to the vendor's Windows app. How a board gets added is in
[docs/boards/README.md](docs/boards/README.md).

Files in the package folders are linked into your home with GNU Stow. The board files are
in `keyboard/.local/share/frameshell/keyboard/boards`, the protocol code in `.../families`;
`tools/` has the converters that make board files from vendor apps.
