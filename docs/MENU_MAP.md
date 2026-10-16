# HR-GP5 menu map (firmware Ver1.03E, found 2026-10-16)

## How the UI works
- **One dispatcher for every screen:** `ui_dispatch` `0x00672`. The screen code ends with
  `MOV.W #table,R0` + `JMP @H'0672`.
  - The row = `table + (ui_page - 1) * 26`. `ui_page` = `FD99` (1-8).
  - Each row is **13 words** (page-0 code addresses): slots 0-11 = keys k0-k11 (see [CONTROLS.md](CONTROLS.md)),
    slot 12 = dial / idle.
  - **Dial moved:** jump to slot 12 with R4 = delta.
  - **Key k pressed** (`FDDC+k` bit 7): clears it, jumps to slot k with R4 = k. For k = 1-6 (A-F) it also stores
    k in `FD9C` (`ui_last_fkey`), with the previous value in R5.
  - **Nothing happened:** jump to slot 12 with R4 = 0.
  - Handlers do their work and jump back to their screen's loop, which ends in the dispatcher again.
- **Global targets:** PLAY = `main_loop` `0x0705F`, EDIT = `0x0AD24`, SYSTEM = `0x0481F`, SAVE = `0x03198`,
  BYPASS = `0x00824`, METER/TUNE = `0x00960`.

## Screens (UI tables)
| table | pages | screen | A-F do |
|---|---|---|---|
| `0x07106` | 1 | **PLAY** (main loop `0x0705F`) | A-F -> `0x07169` with R1 = 0-5 (x 60/68/76/92/120/148): pick a block/field |
| `0x06AF8` | 1 | PLAY variant (meter/tuner?) | A, F, EXIT, dial own handlers; SYSTEM disabled |
| `0x0AD54` | 2 | **EDIT:Select** | per block; EDIT/PAGE flips page |
| `0x0AFD6` | 1 | EDIT:Real Time Controller | MIDI1-4 / PEDAL1-2 sources |
| `0x0B09E` | 1 | EDIT:Structure | |
| `0x0483C` | 1 | **SYSTEM:Select** | A = page set 1 (Set Up), B = Clock, C = Memory, D = page set 5 (Pedal), E = 2 (MIDI1), F = 4 (MIDI2) |
| `0x04912` | 1 | SYSTEM:Memory | Copy / Move / Exchange / Delete / Protect |
| `0x049E4` | 1 | SYSTEM:Clock | |
| `0x04B70` | 1 | SYSTEM:Memory Copy | |
| `0x04CE6` | 1 | SYSTEM:Memory Move | |
| `0x04E60` | 1 | SYSTEM:Memory Exchange | |
| `0x04FB4` | 1 | SYSTEM:Memory Delete | |
| `0x050D2` | 1 | SYSTEM:Memory Protect | |
| `0x0320A` | 1 | **SAVE:Destination Select** | |
| `0x03368` | 1 | SAVE:Name Edit | |
| `0x036F6` | 5 | **page-set engine** (all "settings" pages below) | A-F = slot 0-5 of the current page |
| `0x00C6E` | 1 | ? (TUNER area, `0x0B09`) | |

(`0x04873` = `MOV.B #6,R4; JMP 036AC` (open PDL1) sits between the SYSTEM:Select handlers but no table points at it.)

## The page-set engine (settings pages)
Used by SYSTEM:Set Up, MIDI1/2, Bulk Trans, Pedal, P-CHG Table and EDIT:Output Level / RTC pages.

- **Open:** `R4 = id`, `JMP @H'36AC`. `sub_0185E` looks up `@(0x3B0C + id * 8)` -> descriptor (page 0).
- **State:** `FD8C` = descriptor, `FD98` = page count, `FD99` = page, `FD91` / `FD92` = selected slot / edit flag.
- **Keys:** A-F select slot 0-5 of the current page (`0x037D9`, R1 = page * 6 + slot), dial edits the value,
  EDIT/PAGE = next page (if > 1), EXIT -> where the descriptor says.
- **Redraw:** `sub_03632` (clear, title, slot labels and values).

### Page-set table `0x3B0C` (13 ids x 8 bytes: descriptor pointer + 6-char name)
| id | name | descriptor | title | EXIT to |
|---|---|---|---|---|
| 0 | LEVEL | `0x71C8` | EDIT:Output Level | 1 (`0x0AF00`) |
| 1 | SETUP | `0x723A` | SYSTEM:Set Up | 3 (SYSTEM) |
| 2 | MIDI1 | `0x3B74` | SYSTEM:MIDI1(BASIC) | 3 |
| 3 | BDUMP | `0x3C16` | SYSTEM:MIDI Bulk Trans | 3 |
| 4 | MIDI2 | `0x3C92` | SYSTEM:MIDI2(Receive SW) | 3 |
| 5 | PEDAL | `0x3D52` | SYSTEM:Pedal | 3 |
| 6 | PDL1 | `0x3EB8` | EDIT:RTC(PEDAL1) | 5 (`0x0AFBF`) |
| 7 | PDL2 | `0x3F10` | EDIT:RTC(PEDAL2) | 5 |
| 8-11 | MIDI1-4 | `0x3F68` / `0x4098` / `0x40F0` / `0x4148` | EDIT:RTC(MIDI1-4) | 5 |
| 12 | P-CHG | `0x3DD2` | SYSTEM:MIDI P-CHG Table | 3 |

The table ends where descriptor 2 starts, so **new ids need a relocated table** (patch the word in
`0x0186B: MOV.W @(H'3B0C,R4),R4`).

### Descriptor format (decoded from id 2, MIDI1)
```
+0 byte  pages           +1 byte  EXIT to: 0 EDIT 0AD24, 1 0AF00, 2 PLAY, 3 SYSTEM, 4 036C5, 5 0AFBF
+2 word  title string    +4 word  subtitle string     +6 word  init routine (0x3BC1 = a bare RTS)
+8 ...   6 slots x 8 bytes per page:
         word type/range   word RAM var (page 4)   word label string   word routine / argument
```
| slot type | meaning | example (MIDI1 page) |
|---|---|---|
| `0x80nn` | value 0..nn in RAM var, shown/edited by the routine | A: `8001 6216 " OMNI" 64BB` (OMNI on/off) |
| | | B: `800F 6215 "  CH " 5B61` (channel 0-15) |
| `0x4200` | open page set `arg` | C: `4200 0000 "P-CHG" 000C` -> id 12 |
| | | D: `4200 0000 " B-Tx" 0003` -> id 3 |
| `0x4000` | call routine `arg` | E: `4000 0000 " B-Rx" 67F4` (bulk receive) |
| `0x0000` | empty slot (label `0x7D25` = blank) | F |

Multi-page sets (SETUP, 5 pages) and other range types still need decoding; all routines are page-0 code.

## Hooks for new menus (plan)
- **Cheapest new menu = a new page set.** Write a descriptor + labels, reuse Sony's own value routines (on/off
  `0x64BB`, number `0x5B61`, ...), store values in free RAM, and point a slot type `0x4000` at our own code where
  something should happen. The engine already draws, edits and handles EXIT.
- **Entry point idea:** SYSTEM:Select has no spare key, but its SYSTEM slot (`0x0484E`, k9) just re-opens SYSTEM.
  Pointing it at our page set gives "press SYSTEM twice = ROSETTA menu", like the SP-808 SYS menu.
- **Blocker: ROM space in page 0.** Descriptors, labels, the relocated id table and the table/handler addresses
  must all be in page 0 (EP = 0, 16-bit pointers). Page 1 has 68 B left after Hex girl. Options are in
  [MEMORY_MAP.md](MEMORY_MAP.md#free-space).
