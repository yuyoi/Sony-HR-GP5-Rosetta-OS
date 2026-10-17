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

## Inputs: a second channel is already there (2026-10-17)
- One ADC chip (AK5369, IC301), but the TMS57002 reads **both** of its channels: si0_l and si0_r, each with its own
  gain (c(08) left, c(09) right). Sony sets **both to the same value (0x14300000)**, and the level meters peak both
  (c(0a) / c(0b)). L and R stay separate in this first stage (so0_l / so0_r).
- Parts list: two input op-amp stages (IC101, IC121) and two input mute transistors (Q121, Q122).
- Model byte 'M' (HR-MP5 mode, 0xAC90) changes nothing here: same gains, same program. Its 6 checks are UI limits
  (e.g. a dial max 0x2E vs 0x19 at 0x33CB, a page count 7 vs 5 at 0x4085) and the boot path (0x6A34).
- **The board is shared with the HR-MP5** (photo, 2026-10-17): next to the fitted GP5 input connector there are two
  empty footprints marked **MP5** (CN102 vertical, CN101 along the bottom), the stereo model's input connectors. Both
  input op-amps (IC101, IC121, 4558) and the mutes (Q121, Q122) are fitted and feed the AK5369-VS (IC301).
  The service manual's block diagram labels the inputs "INPUT (FRONT)" and "INPUT (REAR)".
- **2-input mod, three parts:**
  1. Hardware: trace the MP5 pins (underside photo or continuity), split the R stage from the single jack if they
     are bridged, add a second jack.
  2. Effects: unknown whether the chain after the first stage keeps R separate (stereo in / near dual mono), sums
     it (2-input mixer into one chain) or drops it (needs new DSP code; the TMS57002 is full).
  3. Rosetta: per-input level / mute (/ pan) through c(08) / c(09).
- **No-solder test, Rosetta v0.3.3: ROSETTA -> INPUT (IN L / IN R on/off)** mutes c(08) or c(09) alone through Sony's
  live-coefficient routine (page 1 0x156B3: R1 = coefficient, R4:R5 = value; reached from page 0 via a 5-byte stub
  `JSR @0x56B3 ; PRTS` at 0x1F598). Emulator-verified traffic. If the effects still sound with L muted, the chain
  uses R on its own. Hardware result: pending.
- **Live L/R meters, Rosetta v0.3.4: ROSETTA -> INPUT, rows A / B.** The DSP keeps peaks of the raw ADC samples
  (c(0a) = |si0_l|, c(0b) = |si0_r|, before the c(08)/c(09) gains); Sony's 5 ms tick reads them (0x14D9A), maps them
  through a 0..24 table (0x14C95) into **FD87 (L) / FD89 (R)**, peak hold FD86 / FD88. Sony's METER popup shows one
  bar only. Rosetta draws both as 24-segment bars, refreshed every 8 ticks from the idle dial slot. Use: inject a test
  signal anywhere on the input side (MP5 pins, IC101 / IC121) and watch which channel moves.
  **Hardware result (2026-10-17): with the guitar in the front jack both bars move.** The meters work on the real
  unit, and the one jack feeds both ADC channels (bridged somewhere before IC101 / IC121).
- **From the service manual schematic (2-3, main board, 2026-10-17):**
  - Front jack J401 (input board) -> IC401(2/2) buffer -> RV401 INPUT LEVEL -> IC401(1/2) amp (1 + 47k/2.7k, ~+25 dB)
    -> CN401 / CN106 -> main board = **CH1**.
  - **CH1 -> IC101(2/2) unity buffer -> R105 47 -> AINL** (IC301 pin 2).
  - **R1001 = 0 ohm link CH1 -> CH2.** This is the bridge.
  - **CH2 -> IC201(1/2) amp (1 + 47k/5.6k, ~+19.5 dB) -> C210 -> R1002 0 ohm -> IC201(2/2) amp (1 + 47k/8.2k,
    ~+16.5 dB) -> R205 47 -> back-to-back zeners D201/D202 (MA8027) -> AINR** (pin 27).
  - **Equal meter bars are expected (hardware, 2026-10-17: L and R read about the same).** The DSP packs both peaks
    into c(07): L as bits 22..15 (c(0a) & 0x7F8000), R as c(0b) * c(0d) (0x200000 = 0.25) in the bits below. Sony's
    tick takes bits 22..7, so the R byte shows R peak bits 16..9: **6 bits = 36 dB lower than L's window**, which is
    exactly the analog +36 dB. The meters are normalized, so they cannot show the hot channel.
  - So ADC R is **not a spare**: it is the same guitar about +36 dB hotter and zener-clipped. It is most likely
    for the distortions (or the tuner).
  - The rear INPUT (J101, next to the pedal jack) -> IC101(1/2) buffer -> the front jack's switch contact:
    it is the same input, used only when the front jack is empty.
  - **Which effect reads the hot channel? Not visible from the TMS57002.** Emulator: DIST1 on/off = 64 bytes to DSP #2
    only; changing B's type reloads the same TMS57002 program and coefficients (c(08) = c(09) = 0x14300000 for every
    type) plus ~1.8 KB to DSP #2. The distortions live in DSP #2 (unknown instruction set).
  - HR-MP5 (Sound on Sound review): two unbalanced input channels at -10 dBV, two effect blocks in series or
    parallel, and it "allows for the processing of two mono signals" (dual mono). So the same DSP pair does
    dual mono when CH2 is a real second input. Guess: the MP5 board leaves R1001 out and skips the +36 dB stage.
  - Second-input mod = lift R1001 and feed the new jack (through a coupling cap) into the CH2 side. This would
    take the hot channel away from whatever uses it, so first test what ADC R is used for (mute IN R with DIST1).

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
