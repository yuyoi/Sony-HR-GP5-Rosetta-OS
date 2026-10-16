# HR-GP5 controls map (firmware Ver1.03E, found 2026-10-16)

"From code" = read from the firmware, not yet confirmed by pressing the button on the unit.

## Buttons: 3 x 4 key matrix, 12 keys
- **Scanner:** `sub_0129B`. It drives latch `FD01` bits 0-2 low one row at a time and reads the 4 columns from `FD02`
  (read twice to debounce). It's called from the main loops.
- **Key state bytes:** `FDDC + k` (k = 0-11), on-chip RAM:
  - bit 0 = debounce stage, **bit 5 = held now**, **bit 7 = new press (event)**, bit 6 = released (event).
  - `key_read` `0x01225`: R4 = k -> R4 = that state byte (`BTST #5,R4` = held).
  - `sub_0123D` clears all 12 plus the dial deltas.
- **The UI dispatcher** (`0x00672`) consumes bit 7 events, see [MENU_MAP.md](MENU_MAP.md).

| k | row (FD01 bit) | button | handler on most screens |
|---|---|---|---|
| 0 | 0 | METER/TUNE | `0x00960` cycles meter / tuner (state byte `FD9E`) |
| 1 | 0 | A | per screen (slot A) |
| 2 | 0 | B | per screen |
| 3 | 0 | C | per screen |
| 4 | 1 | D | per screen |
| 5 | 1 | E | per screen |
| 6 | 1 | F | per screen |
| 7 | 1 | EDIT/PAGE | `0x0AD24` = enter EDIT; on page sets = next page |
| 8 | 2 | EXIT | `0x0705F` = back to PLAY (does nothing on PLAY) |
| 9 | 2 | SYSTEM | `0x0481F` = SYSTEM:Select |
| 10 | 2 | SAVE | `0x03198` = SAVE:Destination Select |
| 11 | 2 | BYPASS | `0x00824`: toggles RAM `0x462A0`, sends 0x8600 / 0x8601 (DSP), `FDB0` bit 3 |

The k -> button mapping comes from the handlers (SYSTEM opens "SYSTEM:Select", SAVE opens "SAVE:...", BYPASS
toggles the bypass flag, EXIT returns to the play loop, METER/TUNE cycles the meter/tuner state). The matrix rows
follow from the scanner.

## Power-on key combos (from code, `0x0022C-0x00272`)
| Hold at power on | What |
|---|---|
| **A + SYSTEM** (k1 + k9) | runs `0x0406` / `0x0479` (init?) then **factory TEST mode** at `0x05488` (ENC/PDL/SW/JOG checks, Serial Port Check, RAM test, ROM sum) |
| **BYPASS + EXIT** (k11 + k8) | runs `0x0406` / `0x0479`, then boots normally: **probably memory initialise (factory reset)**, not verified, so don't try it with patches you care about |

## Dial (rotary encoder)
- Input port `FD20`, state in `FDEA` / `FDEB` (`sub_0123D` seeds them).
- Deltas: `FDE8` and `FDE9` (signed bytes). `dial_delta` `0x006EF` returns their sum in R4 and clears both.
- In the dispatcher, a non-zero delta calls the screen's slot 12 handler with R4 = delta.

## Other inputs and outputs (from code)
- `FD03`: read by the DSP driver (status / handshake), see [MEMORY_MAP.md](MEMORY_MAP.md).
- `FDB2` bits 7-6 both set -> the main loop calls the BYPASS routine: probably the **bypass footswitch** (pedal jack).
- A/D: `FFE0` = battery (boot check, "Battery Low!!" below 0x71), `FFE2` = a pedal (`sub_00717`, the main loop
  reads it with hysteresis 2).
- `FD00`: control latch for the DSP host interface (also touched by the test mode).
- `FD01` bits 7-4: set from RAM `0x46299` at boot (`sub_00ED2`): LEDs or relays, not traced yet.
