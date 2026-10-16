# HR-GP5 memory map (firmware Ver1.03E, found 2026-10-16)

## Chips (service manual parts list, p. 37-38)
| ref | part | role |
|---|---|---|
| IC506 | **HD6435208F** | CPU: Hitachi **H8/520** (H8/500 family), used in expanded maximum mode |
| IC507 | LE27C1001F-GP5-E1 | 128 KB EPROM, the firmware (socketed, DIP32) |
| IC508 | CXK58257AM | 32 KB SRAM, battery backed (page 4) |
| IC509 | M62021FP | reset / battery backup |
| IC505 | NJU6355EM | real-time clock (SYSTEM:Clock) |
| IC511 | **TMS57002** | the DSP (TI "DASP"; MAME has a core + disassembler) |
| IC513 | LC3517BML | 2 KB SRAM (DSP side?) |
| IC501 / IC510 | M5M411664 / CAT514256 | DRAM (delay memory?) |
| IC502 / IC504 | CXD2705AQ / CXD8481Q | Sony custom (DSP glue / memory controller) |
| IC512 | **SED1335F** | LCD controller |
| IC301 / IC304 | AK5369 / CXD2564AM | ADC / DAC |
| IC101, IC121, IC201, IC221 | uPC4558 | op-amps (input IC1xx, output IC2xx) |
| IC122 | NJM4560 | op-amp |
| IC515 | M66009FP | Mitsubishi serial-in / parallel-out driver? (LEDs?) |
| Q121, Q122, Q221, Q222 | 2SD601A | probably muting transistors (input 1xx / output 2xx); no FETs anywhere in the list |

## CPU address space (maximum mode, 64 KB pages)
| range | what |
|---|---|
| `0x00000-0x0FFFF` | EPROM page 0 (code, UI data, presets). Except: |
| `0x0FD00-0x0FD7F` | external I/O (see below) |
| `0x0FD80-0x0FF7F` | on-chip RAM (512 B): UI state, key bytes, LCD stream |
| `0x0FF80-0x0FFFF` | on-chip registers |
| `0x10000-0x1FFFF` | EPROM page 1 (icons, big font, DSP driver + tables, MIDI) |
| `0x40000-0x47FFF` | 32 KB SRAM, battery backed, stack top `0x48000` |

**Pointer pages:** `@aa:16` and R0-R3 use DP, R4-R5 use EP, R6-R7 (stack) use TP. JSR/RTS stay in the current
code page; crossing pages needs PJSR/PJMP + PRTS. A page-1 routine can't call a page-0 routine that ends in RTS
directly: it needs a page-0 stub (`JSR @x; PRTS`).

## ROM layout (page 0 / 1)
| addr | what |
|---|---|
| `0x00000-0x000FF` | vectors (32-bit), reset `0x0181` |
| `0x00180-0x07FFF` | UI code: boot, LCD API, dispatcher, SAVE/SYSTEM screens, page-set engine, test mode |
| `0x03B0C` | page-set id table (13 x 8 B), descriptors follow (`0x3B74-0x41A0`) |
| `0x07200-0x0AC40` | EDIT pages ("EDIT:Hall Reverb" ...), effect parameter descriptors |
| `0x0A7C2` | small 7-row font (7 B/char) |
| `0x0AB42` | 32 custom glyphs 0x80-0x9F (8 B each, also in CGRAM) |
| `0x0AC88-0x0ACCD` | boot screen strings + icon byte |
| `0x0AD24-0x0B2xx` | EDIT screens |
| `0x0B318` | **100 factory presets x 176 B** (programs 0-99, names at +8) |
| `0x0F7D8-0x0FFFF` | 0xFF, unused (see Free space) |
| `0x10000` | 49 icons, 36x16 (pointer table) |
| `0x112F4` | big 18-row font (pointer table, full ASCII) |
| `0x143FE-0x158xx` | **DSP driver** (FD00 / FD10 / FD11 / FD12 / FD03) |
| `0x15B82` | handler table (index & 0x7FF, `0x1497F`, not decoded yet) |
| `0x1F596-0x1FFFE` | free: now the Hex girl player + frames (68 B left) |

## Effect algorithms (DSP programs)
The H8 builds the TMS57002 program from small blocks in EPROM page 1 and uploads it byte by byte (`sub_155E9`:
EP:R5 -> FD11, inverted, FD10 bit 4 handshake, FP+1 bytes). Every block is a multiple of 3 bytes, which looks like
24-bit DSP instruction words. The upload (`sub_15A3E`):

| block | addr | size | words |
|---|---|---|---|
| header | `0x1A91F` | 6 B | 2 |
| **structure** (program byte 0 & 0x3E -> table `0x1A8A2`) | `0x1A9FD + n * 0x42` | 66 B | 22, **19 structures** |
| common | `0x1A925` | 213 B | 71 |
| tail | `0x1AEE3` | 60 B | 20 |

That's only part of it: the full TMS57002 program also has a 162-word effect-chain block from the rest of page 1,
and the INIT program uses 250 of the 256 instructions (the TMS57002 is full). There's also a second DSP.
Everything about the DSPs is in [DSP.md](DSP.md).

## Programs (`sub_01870`, R4 = program number -> EP:R4 = 176-byte program)
| number | where |
|---|---|
| 0-99 | ROM `0x0B318 + n * 0xB0` (factory presets) |
| 100-199 | RAM `0x413E0 + (n - 100) * 0xB0` (user memories) |
| 200 (`0xC8`) | RAM `0x458A0` (edit buffer?) |
| 201 (`0xC9`) | RAM `0x41280` |

Current program: RAM `0x462A1` (clamped to < 200 at boot), also `FDB4`.

## External RAM (page 4), known so far
| addr | what |
|---|---|
| `0x40000-0x405EF` | LCD shadow, graphics layer (38 B x 40 rows) |
| `0x405F0-0x406AD` | LCD shadow, text layer (38 x 5) |
| `0x41000-0x410FF` | MIDI RX ring (read idx `0x45A0A`, write idx `0x45A08`) |
| `0x41200-0x4127F` | MIDI TX ring (write idx `0x45A0C`), `sub_024E8` puts a byte, `sub_0252F` = MIDI thru |
| `0x41280` | program buffer (#201) |
| `0x413E0-0x4589F` | user programs 100-199 (100 x 176 B) |
| `0x458A0` | program buffer (#200) |
| `0x46215` / `0x46216` | MIDI basic channel / OMNI (SYSTEM:MIDI1) |
| `0x46299` | `FD01` high nibble at boot |
| `0x462A0` | BYPASS flag (bit 7) |
| `0x462A1` | current program |
| `0x462AC` | bulk trans TYPE |
| `0x462E0` | flags (bit 0 cleared by the main loop) |

## On-chip RAM (page 0), known so far
| addr | what |
|---|---|
| `FD8A-FD9C` | page-set engine: `FD8C` descriptor, `FD91` slot, `FD92` edit flag, `FD98` pages, `FD99` **ui_page**, `FD9C` **ui_last_fkey** |
| `FD9E` | METER/TUNE state |
| `FD9F` | BYPASS copy |
| `FDA0` / `FDA1` | set at boot (`FF`, `0`) |
| `FDB0-FDB4` | flags (`FDB0` bit 3 = bypass, `FDB2` bits 7-6 = footswitch?), `FDB4` = program |
| `FDD8` / `FDDA` | LCD stream position / flag |
| `FDDC-FDE7` | key state bytes k0-k11 |
| `FDE8` / `FDE9` | dial deltas |
| `FDEA-FDEC` | encoder state |

## External I/O (page 0)
| addr | what |
|---|---|
| `FD00` | DSP host control latch (strobes) |
| `FD01` | latch: bits 0-2 = key matrix rows (active low), bits 7-4 = ? (LEDs/relays) |
| `FD02` | key matrix columns (read) |
| `FD03` | DSP status / handshake (read) |
| `FD10` / `FD11` | DSP host data / address (write; `FD10` also read) |
| `FD12` | DSP status (read) |
| `FD20` | rotary encoder |
| `FD30` / `FD31` | LCD (SED1335) data / command |

## LCD API (page 0, see FINDINGS.md for the LCD format)
| addr | name | call |
|---|---|---|
| `0x01021` | scr_clear | clear the RAM shadow, draw the title underline |
| `0x01ABD` | txt_str | copy string EP:R4 -> DP:R0 (text layer: `0x5F0 + row * 38 + col`) |
| `0x01147` | gfx_str | small 7-row text, R0 = string, R2 = gfx byte offset |
| `0x010B1` / `0x010A6` | big_str | 18-row text, R1 = string, R2 = column (row 12) / gfx offset |
| `0x0110B` | icon16 | icon R1 at row 12, column R2 |
| `0x0107E` | gfx_fill_rect | R1 = offset, R2 = rows << 8 \| cols, R3 = byte |
| `0x011B7` / `0x011E0` | gfx_blit / _or | bitmap R4, size R1, to R2 |
| `0x04302` | popup_box | framed popup, R2 = col, EP:R4 = string |
| `0x019E1` | delay_units | R0 x ~0.7 ms (guess) |

## Free space
**Rosetta OS v0.1:** the factory presets are gone (programs 0-99 all load one INIT program at `0xB318`), so
`0xB3C8-0xF7D7` (17.4 KB, proven ROM) is free for Rosetta code. v0.1 uses 181 B of it.

- **Page 1:** `0x1F596-0x1FFFE`, used by the Hex girl boot (68 B left).
- **Page 0:** `0x0F7D8-0x0FCFF` (1.3 KB) is 0xFF. The test mode's ROM sum reads page 0 up to `0xFD7F`, so it's
  probably real ROM, but it's not proven: a one-burn probe can check it.
- **More ROM:** if socket pin 30 reaches CPU A17, a bigger chip adds pages 2-3 (128 KB). The SST39SF040 we use is
  already big enough; the same probe can check.
- **Fallback:** the 100 factory presets take 17.6 KB of page 0 (`0xB318-0xF7D7`, they end exactly where the 0xFF
  starts). Giving up a few (e.g. 90-99) frees 176 B each, in proven ROM.
- **Probe build:** `anim_build.py --probe` = Hex girl + after the animation 3 characters on text row 0 for ~2 s:
  `[page 0 0xFA00][page 1 0x1FFFE][page 3 0x1FFFE]`. The x4 SST image has `O` at page 0 `0xFA00` and `1` / `3` at
  `0x1FFFE` in its A17 = 0 / A17 = 1 copies.
  | shows | meaning |
  |---|---|
  | `O1 3` | page-0 tail is ROM, **A17 is wired: +128 KB (pages 2-3)** |
  | `O1 1` | page-0 tail is ROM, A17 not wired (page 3 mirrors page 1) |
  | `O` then `1`/`3` flicker | A17 pin floating on the board |
  | not `O` first | page-0 tail is not readable ROM |

## Patch memory
There's no separate patch RAM: one 32 KB battery-backed SRAM (IC508) holds the user programs, edit buffers, system
settings, MIDI rings and the screen shadow. Factory presets live in the EPROM.
