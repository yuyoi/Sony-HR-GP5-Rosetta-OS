# HR-GP5 DSP dissection (2026-10-16)

How the sound is made, where the DSP code lives, and what Rosetta can change. Found by disassembling the H8 side,
logging every byte the firmware sends to the DSPs in the emulator (`tools/dsplog.py`) and disassembling the
TMS57002 code with a port of MAME's disassembler (`tools/tms57dis.py`).
"Confirmed" = seen in the logged traffic / disassembly. "Likely" = inferred.

## The chips
| chip | role |
|---|---|
| **TI TMS57002** (IC511) "DASP" | DSP #1. 256 instructions per sample, 24-bit data, 256 coefficients (cmem), 256 + 32 data words, external delay RAM port. Programmed by the H8 for every structure. **Confirmed.** |
| **Sony CXD2705AQ / CXD8481Q** (IC502 / IC504) | One of them is DSP #2 (Sony's own instruction set, 32-bit words), the other likely glue / memory control. **Likely.** |
| DRAM M5M411664 + CAT514256, SRAM LC3517 | delay / reverb memory and DSP-side RAM. **Likely.** |
| AK5369 ADC, CXD2564 DAC | converters |

## Host interface (both DSPs hang off the H8's I/O at FD00-FD12)
- `FD10` selects the target: **4 = TMS57002, 7 = DSP #2**. `FD11` = data byte, `FD10` bit 4 = handshake.
- **TMS57002** (confirmed, matches MAME's tms57002 core): each byte goes out as `FD11 = E0` then `FD11 = ~data`.
  `FD00` bit 1 = /PLOAD, bit 0 = /CLOAD (low = active):
  - PLOAD: 3-byte words: ST0, ST1, then program words from address 0.
  - PLOAD + CLOAD: 4-byte words straight into coefficient memory.
  - CLOAD: address byte + 4-byte value = live coefficient update (the DSP swaps it in between samples).
- **DSP #2** (confirmed protocol, unknown instruction set): `FD00` bit 4 low = address bytes, high = data bytes.
  Addresses `0x30xx` = program chunks, `0x2Cxx` = coefficient / data chunks. Words are 32-bit, stored big-endian
  in ROM (e.g. a 44-word chunk at `0x1A7B8`) and sent byte-reversed.

## TMS57002 program = 4 pieces (confirmed for the INIT program)
| words | piece | ROM |
|---|---|---|
| 0-21 | **structure block** (one of 19, picked by program byte 0) | `0x1A9FD + n * 0x42` |
| 22-92 | **common block** (inputs, meters, mixing) | `0x1A925` |
| 93-254 | **effect-chain block** (162 words) | e.g. `0x1E25E`, picked through tables in `0x1C8xx` |
| 255 | last word | |

**The TMS57002 is full**: 250 of 256 instructions are active, only 6 NOPs. (Earlier I said ~140 were spare. That
was wrong: I had only found two of the pieces.) Instruction mix: 58 `mac`, 35 `lacc`, 22 `mpy` ... it's a
multiply-accumulate filter / mixer machine. It reads serial inputs si0 (L/R) and si1 (L/R) and writes so0 / so1, so it
sits in a serial audio chain with DSP #2. It uses external memory (`rde` x4, `wre` x1): one short delay line.

ST0 = `010820`, ST1 = `000820` (status / mode words sent first).

## Live control from the H8 (confirmed)
- On a program change, the H8 sends the program (if the structure changed), then coefficient sets for every
  parameter (556 updates at boot).
- **It keeps streaming while you play:** in PLAY, `c(06)` is updated ~500 times a second (a modulation value the CPU
  computes) and `c(0a)` / `c(0b)` are reset ~250 times a second (level-meter peaks the DSP writes and the H8 reads).
- Parameter -> coefficient: ~80 page-1 routines (handler table `0x15B82`), and descriptor tables at `0x1A766`
  (entries `0x1AF20 / 0x1B06C / 0x1B2A8 / 0x1B4AE`) that list per block: count, address, coefficient source.
- The edit buffer (RAM `0x41280`) is diffed against the DSP-side copy (`0x41330`); only changes are sent.

## Where the DSP data lives (page 1)
| range | what |
|---|---|
| `0x1A766-0x1A91E` | descriptor / pointer tables, DSP #2 chunks (e.g. `0x1A7B8`) |
| `0x1A91F-0x1AF1E` | TMS57002 ST0/ST1, common block, 19 structure blocks, a 15-word cmem init |
| `0x1AF1F-0x1F595` | ~18 KB: TMS57002 effect-chain blocks (162-word sets), DSP #2 code and tables, coefficient curves |

## What's possible
| idea | how | effort | risk |
|---|---|---|---|
| **LFOs / envelopes / step-seq on any parameter** | H8 streams coefficient updates (the firmware already does it for c(06)); Rosetta adds sources and targets | low-medium | low |
| **Tempo: tap, MIDI clock** sync for delays / LFOs | H8 side, then coefficient updates | medium | low |
| **Extended parameter ranges** (more feedback, longer times, extreme EQ) | program bytes + the param->coefficient routines | low-medium | medium (loud) |
| **Hidden / alternate structures** | 19 structure blocks + effect blocks are data: new combinations | medium | medium |
| **New TMS57002 effects** (e.g. bitcrush, ring mod, waveshaper, gate, filters) | write TMS57002 code (needs an assembler, the reverse of tms57dis) and swap it into an effect-chain block of the same size or smaller | medium-high | medium |
| **New DSP #2 effects** (reverb / long delay side) | needs DSP #2's instruction set reverse-engineered first (we have its code and the protocol) | high | medium |
| Freeze / glitch / reverse | TMS57002 delay memory + H8 control | high | medium |

**Best first step:** a Rosetta modulation engine (LFO, tempo) driving coefficient updates. It needs no new DSP code,
it reuses a path Sony already uses, and it gives every stock effect new life.
