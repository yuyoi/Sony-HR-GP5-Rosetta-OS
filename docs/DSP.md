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
- **It keeps streaming while you play:** in PLAY, `c(06)` is rewritten ~500 times a second (the host-read pointer, 7, see Inputs) and `c(0a)` / `c(0b)` are reset ~250 times a second (level-meter peaks the DSP writes and the H8 reads).
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
| **LFOs / envelopes / step-seq on any parameter** | H8 streams coefficient updates (the firmware already streams live coefficients); Rosetta adds sources and targets | low-medium | low |
| **Tempo: tap, MIDI clock** sync for delays / LFOs | H8 side, then coefficient updates | medium | low |
| **Extended parameter ranges** (more feedback, longer times, extreme EQ) | program bytes + the param->coefficient routines | low-medium | medium (loud) |
| **Hidden / alternate structures** | 19 structure blocks + effect blocks are data: new combinations | medium | medium |
| **New TMS57002 effects** (e.g. bitcrush, ring mod, waveshaper, gate, filters) | write TMS57002 code (needs an assembler, the reverse of tms57dis) and swap it into an effect-chain block of the same size or smaller | medium-high | medium |
| **New DSP #2 effects** (reverb / long delay side) | needs DSP #2's instruction set reverse-engineered first (we have its code and the protocol) | high | medium |
| Freeze / glitch / reverse | TMS57002 delay memory + H8 control | high | medium |

## Inputs and the second-input research (2026-10-17, corrected after a hardware test)
**Signal chain (service manual schematic 2-3 + block diagram 2-1, read from the scans):**
- Front jack J401 (input board) -> IC401(2/2) buffer -> RV401 INPUT LEVEL -> IC401(1/2) amp (1 + 47k/2.7k, ~+25 dB)
  -> CN401 / CN106 -> main board = **CH1**.
- **CH1 -> IC101(2/2) unity buffer -> R105 47 -> AINL** (AK5369 IC301 pin 2).
- **R1001 = 0 ohm link CH1 -> CH2.** This is the bridge. Hardware: lifting it leaves 170k between IC101 pin 3 and IC201 pin 3
  (= R103 10k + R202 100k through ground plus in-circuit drift), so no other copper bridge.
- **CH2 -> IC201(1/2) amp (1 + 47k/5.6k, ~+19.5 dB) -> C210 -> R1002 0 ohm -> IC201(2/2) amp (1 + 47k/8.2k, ~+16.5 dB)
  -> R205 47 -> back-to-back zeners D201/D202 (MA8027) -> AINR** (pin 27). This is the same guitar ~36 dB hotter and
  zener-clipped. The chip markings on the board match (R211 562, R202 104, R212 473).
- Rear INPUT (J101, next to the PEDAL jack) -> L101 -> C101 -> IC101(1/2) buffer -> the front jack's switch contact. It is
  the same input, used only when the front jack is empty.
- **The AK5369's SDATA goes to IC502 (Sony's DSP, SIA pin 40), not to the TMS57002.** IC502 SOA/SOB feed the TMS57002,
  which returns on SIB. So **which effect uses the hot channel is decided inside IC502** (unknown instruction set). The
  board is shared with the HR-MP5 (empty MP5 connector footprints CN101/CN102). Sound on Sound: the MP5 has two
  -10 dBV inputs and "allows for the processing of two mono signals", so the same DSP pair can do dual mono.

**Corrections (my earlier firmware claims were wrong):**
- The TMS57002's si0/si1 come from IC502, not from the ADC. So **c(08) / c(09) are not ADC input gains** (they scale
  si0_l / si0_r from IC502 into so0), and **the v0.3.3/0.3.4 INPUT page mutes are not ADC mutes.**
- **The meters are not ADC L / ADC R.** MAME semantics: `lira` = id <- ACC >> 24. ACC = c(05) = 0x21000000, so the meter
  code peaks **d(21) and d(22), both stored from si1_l** (words 17 and 22). c(07) packs them (bits 22..15, plus the second
  * 0.25 below), `lpc c*` with ca = c(06) >> 24 = 7 hands c(07) to the host, and Sony's tick splits it into FD87 / FD89. So
  FD87 / FD89 are the **same si1_l signal from IC502, coarse and 36 dB finer**, not two channels. This matches the
  hardware: both bars equal, and unchanged with R1001 lifted or IC201's input grounded.
- c(06) is not a modulation value: it is the host-read pointer (7) that the tick rewrites.
- The 38-tap FIR (words 51-88) runs on si1_r.

**HR-MP5 service manual (same board 1-649-450-13, printed "HR-GP5 / HR-MP5"; scans read 2026-10-17):**
| part | GP5 | MP5 |
|---|---|---|
| EPROM IC507 | LE27C1001F-**GP5**-E1 | LE27C1001F-**MP5**-E1 (different firmware) |
| R1001 (CH1 -> CH2 link) | 0 ohm | not fitted |
| CH2 input: R1003 0, L201 4.7 uH, C201 22 uF, R201 100k | not fitted (bare pads) | fitted: CH2 jack -> R1003 -> L201 -> C201 -> IC201(1/2) |
| IC201(1/2) | amp x9.4 (R212 47k / R211 5.6k) | buffer (R212 0 ohm) |
| IC201(2/2) | amp x6.7 (R214 47k / R213 8.2k) | buffer (R214 0 ohm) |
| D201/D202 zeners | fitted | not drawn |
| R1002 (IC201 stage to stage) | 0 ohm | CH2 leaves through the input board (RV101 gang 2 -> IC102(2/2)) and comes back |
| rear jacks J101 | PEDAL + INPUT (REAR, into the CH1 path L101) | CH1/MONO (L101) + CH2 (R1003 -> L201); CH2's switch normals CH1 into CH2 |
| front | guitar INPUT + RV401 + IC401 | pedal jack; dual level pot RV101 + amp IC102 |

**Hardware result (2026-10-17): the stock HR-MP5 Ver1.11 ROM (an owner's dump, not in this repo) boots and runs on
this HR-GP5.** (One false alarm on the way: a stray solder blob on the CPU's pins 33-35 gave backlight + blank screen.)

**MP5-style second input on the GP5 (Sony's own footprints):** R1001 out (done); fit R1003 0 ohm, L201 4.7 uH (or a link),
C201 22 uF/16 V, R201 100k; feed the jack's tip into the R1003 pad and break its old route to L101 (beep the tip to both
pads first); choose the CH2 gain with R212 / R214 (0 ohm = MP5 unity for line level; stock = +36 dB clipped).

**Meters that can (Rosetta v0.3.5, ROSETTA -> INPUT):**
- **ADC L / ADC R = the raw analog channels via the CPU's own A/D.** IC506 (HD6435208A00F) pins: 45 AN0 (VBAT), 46 AN1
  (PEDAL), **47 P62/AN2 and 48 P63/AN3 tied to AVSS (unused)**, 49 AVCC, 44 AVSS. Sony sets ADCSR = 0x31 at 0x0208
  (scan AN0-AN1); Rosetta patches 0x33 (scan AN0-AN3), so ADDRC/ADDRD (FFE4/FFE6) follow AN2/AN3 and the pedal and
  battery readings are unchanged. The idle loop keeps min/max, and every 8 ticks peak-to-peak becomes 24 segments (2 dB).
  Hardware: free pins 47/48 from AVSS, then per channel: tap (AINL = IC301 pin 2 / AINR = pin 27) -> 1 uF -> ~4.7k ->
  pin, with 10k to AVCC (pin 49) and 10k to AVSS for a 2.5 V bias.
- **DSP A / DSP B = Sony's meter, re-aimable.** SOURCE writes c(05): SI0 (si0_l / si0_r), SI1 L (Sony's), SI1 R (si1_r).
  This shows what IC502 forwards, no hardware needed.

**What is left for the 2-input mod:** a hardware split is done (R1001 lifted). The deciding test is by ear: does DIST1
(and the rest) sound the same with R1001 lifted? Same = the hot channel is unused by these programs and CH2 is free for
input 2. A scope on AINR (IC301 pin 27) shows the hot channel directly.

## Rosetta LFO (v0.3, emulator-tested)
- Hooks the 5 ms system tick (vector 0xC0 -> `lfo_isr` -> Sony's handler). Every 2nd tick: phase += rate,
  wave (triangle / sine / square / saw / random), value = base + depth * wave, clamped to the parameter's range.
- **Written into the DSP-current program (RAM 0x41280); Sony's own driver diffs it against 0x41330 and sends it**,
  with Sony's own parameter scaling. base = the edit buffer (0x458A0), so SAVE never stores a modulated value.
  Only writes when the value changes; switching off restores the original.
- Target = block A-F + its n-th editable parameter, found through Sony's EDIT table (0x8044: descriptor + name).
  Program blocks: A 0x3C, B 0x44, C 0x4C, D 0x5C, E 0x78, F 0x94; byte base+1 = effect type.
- Heavy targets: some parameters (e.g. HALL reverb time) make Sony re-send ~1 KB to DSP #2 per change; modulate
  those slowly. Rate is 0.05-15 Hz assuming a 5 ms tick (to calibrate on hardware).

**Best first step:** a Rosetta modulation engine (LFO, tempo) driving coefficient updates. It needs no new DSP code,
it reuses a path Sony already uses, and it gives every stock effect new life.

## Effect sets: HR-GP5 vs HR-MP5 (2026-10-17, from the EDIT tables)
| | HR-GP5 1.03E (6 blocks in series) | HR-MP5 1.11 (2 chains: EQ -> EFCT, EQ -> EFCT) |
|---|---|---|
| dynamics | A: COMP, LIMIT, S.ATK | in EFCT1/2: COMP, LIMIT, **GATE**, S.ATK |
| drive | B: CRNCH, OVD1-4, DIST1-2 (7) | EFCT2: one DIST, one OVD |
| amp | F: AMP/S x4 (cab sims) | EFCT1: one AMP |
| EQ / filter | C: E Q, EXCIT, WAH | 2 EQ blocks + EXCIT, WAH, **4PEQ, DyFIL** |
| modulation | D: CHORS FLANG PHASE VIBRT TREML A.PAN RING ROTRY, **D.CHO D.FLN D.PHA StENS**; E: **StCHO StFLN StPHA** | EFCT1: CHORS FLANG PHASE VIBRT TREML A.PAN RING ROTRY, **ENS** |
| pitch | D: I.PCH, **D.PCH**; E: PITCH | EFCT1: I.PCH, PITCH |
| delay | D/E/F: MdDLY StDLY TpDLY DbDLY HdDLY SwDLY TmDLY DcDLY | both EFCTs: the same set |
| reverb | F: HALL, ROOM, PLATE, GtREV | EFCT2: HALL, ROOM, PLATE, GtREV, **DcREV** |
| special | D: REVRS | EFCT1: REVRS, **FREEZ, SHG**; EFCT2: **VoCNL** |

**Reverb uploads (emulator capture of the DSP #2 traffic on a type change, `dsplog.decode`):**
- Inside one firmware, HALL and ROOM are **the same DSP #2 program**: the GP5 upload is 173 chunks / 1490 B either way and only
  **20 4-byte words differ** (MP5: 34 words). PLATE also changes one 372 B and one 54 B chunk. GtREV is a bigger upload (2.5 KB).
- The 20 words are not in the ROM as-is: the H8 computes them (the high 16 bits vary and the low 16 bits look like opcode fields,
  probably delay-tap offsets and coefficients). **New reverb voicings = change those words**, with no need to know DSP #2's
  full instruction set.
- GP5 HALL vs MP5 HALL: different uploads (173 vs 148 chunks, 21 shared chunks; the MP5 sends one 724 B program chunk).
  The MP5's reverb is a different build of the code (relocated or rewritten for the dual chain). By ear it's unknown.

## KEYS sampler / synth on the TMS57002: feasibility (2026-10-17, from MAME's core + Sony's ST0)
Idea: the SP-808 Rosetta KEYS mode (`sp808/tools/keysdsp.py`): our own program in the effects DSP records the input into
its delay RAM and plays N pitched voices from it, with the H8 sending only pitch / gate / level coefficients. Here the
TMS57002 is the target (MAME has a full core; DSP #2's instruction set is unknown).
- **Delay RAM = 64K 16-bit samples (~1.4 s at 44.1-48 kHz).** Sony's ST0 `010820` = M_256K, 4-bit bus (SEL = 0),
  16-bit words (WORD = 0): a 256K x 4 DRAM (CAT514256, IC510). Address = (xoa + xba) * 4 nibbles, xba counts down 1 per sample.
- **External memory speed:** one access at a time, one nibble per instruction: a 16-bit read or write takes 4 instruction
  cycles. A `rde` / `wre` issued while one is still in flight is silently dropped. Up to ~60 accesses per sample.
- **Computed read addresses work:** `rde c` reads at xoa = cmem[c], and `sacc c` writes the accumulator into cmem from
  the program. So each voice computes its own tap (AGE - phase) every sample. `srbd d` stores the read result.
- **3 operations per instruction word:** cat2-pre (stores: sacc / sacd / srbd / lira / dis) + cat1 (ALU / MAC / rde / wre)
  + cat2-post (dos = output) all run in one word. So a voice is far fewer words than on the SP-808's ESP2.
- Host updates (`CLOAD` coefficient writes) queue (16 deep) and apply when the program reads that coefficient: fine for
  pitch / gate / level per note. ST1 CRM can read a coefficient's high or low 16 bits (integer / fraction split).
- **Estimate (my draft, not assembled yet):** about 16-20 words per interpolated sample voice and ~30 words of
  globals (input, record write, mix, filter, output) gives **~10-12 sample voices**. Pure oscillator voices (DDS phase,
  saw / pulse / triangle via abs and saturation) need no RAM and fewer words. Stereo: a pan per voice into L / R sums,
  out on so0_l / so0_r.
- While KEYS owns the TMS57002, Sony's TMS effects are gone (the chip is full) and Sony's driver writes must be gated
  (SP-808: Roland's DSP1 writes gated the same way).

Steps: (1) `tms57asm.py` (from `tmsinstr.lst` encodings, round-trip through `tms57dis.py`) + `tms57sim.py` (Python port of
the MAME semantics incl. delay RAM and coefficient updates), checked on Sony's captured program; (2) a KEYS program
generator + rendered WAV tests; (3) H8 side: program upload, gate Sony's TMS writes, a Rosetta KEYS page that plays test
notes from the panel, first hardware burn; (4) MIDI note-on hook; (5) oscillators, filter, envelopes, LFO, ARP.
