# HR-GP5 findings (working notes)

Everything here came from disassembling the stock EPROM (Ver1.03E). Addresses are CPU addresses (= EPROM offsets).

## EPROM dump (2026-10-16)
- Chip: LE27C1001F (128K x 8, DIP32), chip ID 0x2005 (reads as ST M27C1001).
- Read with a T48: `minipro -p "M27C1001@DIP32" -y -r dumps/hrgp5_eprom_read1.bin` (minipro has no LE27C1001, the ST part works).
- Two reads were identical, MD5 4DB502640E1A799B2114D42262DBDFF9.

## Hardware (from code)
- CPU: Hitachi H8/500 family, maximum mode (PJSR/PRTS, 1 MB space). CONFIRMED (whole ROM decodes clean).
  On-chip RAM FD80-FF7F + regs FF80-FFFF = H8/520-like. No watchdog use.
- ROM: page 0 = 0x00000-0x0FFFF, page 1 = 0x10000-0x1FFFF (code in both, page 1 via PJSR).
- External RAM: page 4 (0x40000-0x47FFF), SP = 0x48000 (TP=4).
- External I/O in page 0 at FD00-FD7F:
  - **FD30 = LCD data, FD31 = LCD command (Epson SED1330-class)**.
  - FD01 = output latch (high nibble set by sub_00ED2).
- Pointer pages: @aa:16 and R0-R3 use DP, R4-R5 use EP, R6-R7 use TP.

## Vectors
| vec | handler | what |
|---|---|---|
| 0x00 reset | 0x0181 | init |
| 0x80 IRQ0, 0x40 TRAPA#0 | 0x04A9 | LCD streamer (sub_00EF0, 30 B per IRQ) |
| 0xC0, 0x44 TRAPA#1 | 0x04FD | ? |
| 0xB4 | 0x05C7 | timer -> sub_02486 |
| 0xD0/D4/D8 | 0x0535/057B/05AB | serial ch A (ERI/RXI/TXI) |
| 0xE0/E4 | 0x0558/058F | serial ch B (ERI/RXI), one of A/B = MIDI |
| others | 0x0180 | RTE |

## Boot flow
- 0x0181: set TP/SP/DP/EP, SR=0700, on-chip regs, JSR 02BD, 048C.
- 0x022C-0x0272: held-key checks (sub_01225 reads keys, bit 5 = pressed).
  Combo 1 (keys 0B+08): JSR 0406/0479 then normal boot (probably an init/reset).
  Combo 2 (keys 01+09): PJMP **0x5488 = TEST mode** ("TEST: Serial Port Check", RAM test, ROM sum). Unverified on hw.
- 0x05E4: main init. sub_00300 inits the LCD (sub_00F18). **0x0637 IRQs on.**
- **0x063E JSR @H'2558 = boot screen** (only caller). 0x0641 battery check ("Battery Low!!" popup).
- 0x0653: hold the boot screen for 700 delay units (sub_019E1, 0x190 loop per unit). 0x065F JMP 705F = main loop.

## LCD (SED1330-class)
- Init (sub_00F58): SYSTEM SET 30 85 07 25 80 27 26 00 -> **228 x 40 px**, 6x8 cells, 38 bytes/row,
  SCROLL: text layer @ VRAM 0x05F0 (38x5 chars), graphics layer @ VRAM 0x0000 (38 B x 40 rows).
  OVLAY 01 = text XOR graphics. CGRAM: chars 0x80-0x9F at VRAM 0x0700 (uploaded from ROM 0xAB42).
- Graphics byte = 6 pixels, MSB first, bits 1-0 unused.
- Firmware draws into a **RAM shadow at 0x40000** (0x000-0x5EF gfx, 0x5F0-0x6AD text) and the IRQ0 handler
  streams it to VRAM 30 bytes at a time (position in FD80+ var 0xFDD8).
- Fonts/art:
  - small 7-row font 0xA7C2 (7 B/char, used for gfx text by sub_0115E)
  - custom glyphs 0xAB42 (8 B/char, codes 0x80-0x9F, box corners 0x80-0x87)
  - **big 18-row font**: pointer table 0x112F4 (page 1), full ASCII; glyph = width byte + 18-byte columns
  - **16-row icons**: pointer table 0x10000, 49 icons of 36x16 px (effect icons); icon 0 = guitar on the boot screen
- Boot screen (sub_02558): clear (sub_01021, also draws the title underline at gfx row 8), then
  text "Guitar Processor" 0xAC92 (row 0 col 11), icon #[0xACCD] at gfx row 12 col 7,
  big "HR-GP5" 0xACA3 at row 12 col 13, small "Ver1.03" 0xAC88 + "E" 0xB316 at gfx off 0x339,
  "Copyright (C) 1993,1994 SONY Corp." 0xACAA (text row 4 col 2).
- 0xAC90 = model byte 'G'; code checks 'G' vs 'M' in 6 places: **this firmware is shared with the HR-MP5**.
- Render: `python -I tools/gp5lcd.py dumps/hrgp5_eprom_read1.bin preview/boot_stock.png` (matches the real screen).

## Free space
- **0x1F596-0x1FFFE (2665 B), page 1: safe** (test-mode ROM sum reads all of page 1).
- 0x0F7D8-0x0FCFF, page 0: probably safe (ROM sum reads page 0 up to FD7F), unused so far.
- 0x1FFFF = test-mode checksum byte. Stock does not match it either (sum 0x74 vs 0xFF), so leave it.
- Bigger EPROM? If socket pin 30 goes to CPU A17, a 27C020 gives +128 KB (pages 2-3). Check the board.

## Easy mods (no code)
- Same-length (or shorter + 00) string edits: AC92 title, ACA3 big text (any ASCII), AC88 version, ACAA copyright.
- 0xACCD = which icon (0-48) shows next to the big text.

## Boot animation pipeline (2026-10-16, works on hardware)
- Hook: 0x2558 `18 10 21 04 04 8d` -> `03 01 F5 A0 00 00` (PJSR player, the player does the replaced clear + DP).
- Player @ 0x1F5A0 (325 B): masks IRQs, decodes each frame into the RAM shadow, blits RAM -> VRAM itself,
  waits hold x tick units, then restores clear screen, LCD stream state, SR, regs, PRTS. The stock boot screen then draws.
- Frame stream = codec v2 (`tools/codec2.py`): hold byte (0 = end, |80 = replay an earlier frame by address) + tokens
  over the previous frame: skip, long skip, end-of-frame, fill, copy-from-ahead (row shifts / 1-byte shifts), XOR literals.
- `python -I tools/anim_build.py <anim.gif | png_dir> [--tick 0x4B] [--hold N] [--out build/x.bin]`
  - frames must be 228x40, dark = dot on. GIF durations -> holds (--ms-per-tick, default 50).
  - checks: stock MD5, hook bytes, free space, Python decode == input, **emulates the real player bytes**
    (frames out of emulated VRAM == input, exit state == stock), writes `_player.lst` + `_preview.gif`.
- Template: `frames/TEMPLATE_blank_228x40.png`.
- **ROSETTA boot (Hex girl, from the SP-808 Rosetta OS)**: `frames/rosetta_boot.gif` (45 frames 132x32 from the SP-808
  repo's `assets/roland_chan_frames.npz`, centred at x 48 y 4; `tools/sp808_boot.py` remakes it).
  Build MD5 be5c78deb229bd1a87a16cfc55549db0 (from the Ver1.03E dump above).
  43 frames, 3 replays, 2262 B stream, 68 B left. Changes vs stock: only 0x2558-0x255D + 0x1F5A0-0x1FFBA.
  `_SST39SF040_x4.bin` = the same image x4 (MD5 75aa89fc...), so the SST's A17/A18 don't matter in the 27C010 socket.
  Burned to an SST39SF040 (T48 verify + readback OK) and **runs on a real HR-GP5**, no socket mod.
- Budget: about 2.3 KB of frame data. Small moving parts and whole-byte shifts are cheap; full new screens cost up to 1.5 KB.
- Timing: 1 tick = 0x4B stock delay units (a guess for 50 ms, the SP-808 tick); the stock boot screen = 700 units.
  Not timed on hardware yet. Rebuild with another --tick if needed (bigger = slower).

## Next
- Time the boot animation on hardware and calibrate --tick.
- SysEx parser (serial RX ISRs 058F/057B) for hidden hooks; DSP chip ID + microcode upload.
- Bigger EPROM: check whether socket pin 30 reaches CPU A17 (a 27C020 would add pages 2-3).
