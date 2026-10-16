# Sony HR-GP5 Rosetta OS  (WIP)

<p align="center"><img src="docs/hexgirl_boot.gif" width="684" alt="Hex girl boot animation on the HR-GP5 LCD"></p>

A custom boot for the **Sony HR-GP5** (1994 half-rack guitar processor, same hardware as the HR-MP5). **Hex girl**,
the Rosetta mascot, slides up on the 228 x 40 LCD, blinks, smiles, then "JSW OS" and "ROSETTA" type in. After that the
stock HR-GP5 screen appears and the unit runs as normal. It's the same boot as the
[SP-808 Rosetta OS](https://github.com/yuyoi/Sp-808-rosetta-looper-OS-), now running on a Sony.

**It runs on a real HR-GP5.** One chip swap in the stock socket, no board mod.

> **Made by JunkSmithWizard (JSW) together with Claude (Anthropic's AI).** Claude did the reverse engineering
> (H8/500 disassembly, LCD and boot-flow mapping) and wrote the tools, the frame codec and the H8/500 player.
> JSW dumped the EPROM, drew Hex girl, called the shot to bring the Rosetta boot over, and burned and tested it on a
> real HR-GP5. From EPROM dump to Hex girl on the screen in one day.

**Legal:** this repo contains no Sony code or ROM data. You dump **your own** unit's EPROM and the tools patch it on
your PC. Don't share dumps, builds or disassembly listings: share this repo instead.

## Status

| What | Status |
|---|---|
| Hex girl boot animation, then the stock boot screen | **hardware** |
| Your own boot animation: any 228 x 40 GIF in, burnable ROM out | built, emulator-verified (same player) |
| Firmware map: CPU, LCD, boot flow, fonts, free space ([docs/FINDINGS.md](docs/FINDINGS.md)) | done |
| Animation speed | not timed on hardware yet (a guess at 50 ms per step) |
| Hidden SysEx hooks, new DSP effects | to do |

## How it works

- **CPU:** Hitachi H8/500 family in maximum mode, firmware in one 128 KB EPROM (LE27C1001, DIP32).
- **LCD:** an Epson SED1330-class controller, 228 x 40 pixels, 6 pixels per byte. The firmware draws into a RAM copy of
  the screen and an interrupt streams it to the LCD.
- **The patch:** 6 bytes in the boot-screen routine call a 325-byte player in the EPROM's free space (about 2.6 KB
  is free at the end). Nothing else in the firmware changes.
- **The player** masks interrupts, decodes each frame into the firmware's own screen buffer, pushes it straight to
  the LCD, waits, and at the end hands the screen back to the firmware exactly as it found it. Then the stock boot
  screen draws.
- **The codec** ([tools/codec2.py](tools/codec2.py)) codes each frame as changes against the previous one: skips,
  fills, row and byte shifts (copy from ahead), XOR bytes, and replays of earlier frames. Hex girl's 45 frames go
  from 9.2 KB raw to 2.3 KB.
- **Every build is checked** before it's written: the builder runs the real player bytes in a small H8/500
  emulator and compares every frame that reaches the emulated LCD with your input.

## Quick start

1. **Dump your EPROM.** With a T48: `minipro -p "M27C1001@DIP32" -y -r dumps/hrgp5_eprom_read1.bin`.
   The tools expect firmware **Ver1.03E** (MD5 `4DB502640E1A799B2114D42262DBDFF9`) and refuse anything else.
2. `pip install pillow numpy`
3. `python tools/anim_build.py frames/rosetta_boot.gif --out build/hrgp5_rosetta.bin`
4. **Burn a new chip and keep your original EPROM.**
   - 27C010 / 27C1001: burn `build/hrgp5_rosetta.bin`.
   - **SST39SF040** (what we used): burn `build/hrgp5_rosetta_SST39SF040_x4.bin`, the same image 4 times, so the
     chip's two extra address pins don't matter in this socket: `minipro -p SST39SF040 -w build/hrgp5_rosetta_SST39SF040_x4.bin`.
5. Power off, swap the chip (notch the same way round), power on.

## Make your own boot animation

- Draw 228 x 40 frames, 1-bit (dark = pixel on). Start from `frames/TEMPLATE_blank_228x40.png`.
  Save an animated GIF (frame durations are used, in 50 ms steps) or a folder of numbered PNGs.
- `python tools/anim_build.py my_anim.gif --out build/my_boot.bin`. It writes the ROM, an x4 SST image,
  a preview GIF and the player listing.
- **Budget: about 2.3 KB after compression.** Small moving parts are cheap. Moves in whole rows, or sideways in steps
  of 6 px (one LCD byte), are nearly free. A completely new screen or a full flash costs up to 1.5 KB. The builder
  says how much is left, or how far over you are.
- `--tick` sets the speed (default `0x4B`, a guess at 50 ms per step; bigger = slower).

## Tools

| File | What |
|---|---|
| `tools/anim_build.py` | frames in, patched ROM out (+ x4 SST image, preview, listing); all the checks above |
| `tools/codec2.py` | the frame codec (encoder, reference decoder) |
| `tools/gp5lcd.py` | LCD model; renders the stock boot screen from your dump to a PNG |
| `tools/h8500dis.py` | H8/500 disassembler (Python port of MAME's) with code tracing |
| `tools/mklisting.py` | full traced listing of your dump (keep it to yourself) |
| `tools/sp808_boot.py` | remakes `frames/rosetta_boot.gif` from the SP-808 repo's frames |
| `docs/FINDINGS.md` | everything we found: memory map, vectors, boot flow, LCD, fonts, free space, the patch |

## Legal

Not affiliated with or endorsed by Sony. "Sony" and "HR-GP5" are trademarks of Sony Group Corporation. This repo
contains **no Sony code or data**: you build from your own EPROM dump. Hex girl art by JSW. `tools/h8500dis.py` is a
port of MAME's H8/500 disassembler (BSD-3-Clause, AJR). Burn at your own risk, and always keep your stock EPROM.

License: MIT (see `LICENSE`).

---

**Thanks for enjoying, and God bless you!** — JSW
