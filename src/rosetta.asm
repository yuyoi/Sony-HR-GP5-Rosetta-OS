; Sony HR-GP5 Rosetta OS v0.3 - page-0 code in the freed factory-preset area (0xB3C8-0xF7D7).
; Assembled by tools/h85asm.py from tools/rosetta_build.py.
;
; Classic Mac page engine, folder-tab style (mockup G):
;   window frame, black tab with the bold title, status text right, 3 rows, inverted selection, scroll bar.
;   Pages are tables: title, status, items. Item kinds: SUB (opens a page), NUM (0..max), LIST (names),
;   ACT (routine), INFO (label + fixed text).
; Keys: dial = move selection (edit mode: change value), A-F = row 1-6, EDIT/PAGE = open / edit,
;       EXIT = edit off / back / leave, SYSTEM, SAVE, BYPASS, METER/TUNE as everywhere.
; Settings live in battery-backed SRAM 0x47000 (unused by Sony, checked), behind the magic 'RS'.
; Drawing goes straight into Sony's LCD shadow (RAM 0x40000, 6 px per byte); the IRQ0 streamer shows it.

        .equ ui_dispatch, 0x0672
        .equ key_bypass, 0x0824
        .equ key_meter_tune, 0x0960
        .equ scr_save, 0x3198
        .equ scr_system_select, 0x481F
        .equ draw_begin, 0x04C5
        .equ draw_end, 0x04CD
        .equ ui_poll_a, 0x1B03
        .equ ui_poll_b, 0x0790
        .equ ui_after_draw, 0x43AD
        .equ FONT, 0xA7C2                ; small 7-row font, 7 bytes per char
        .equ ROWB, 38                    ; bytes per pixel row (6 px per byte)

; ---- battery-backed RAM (page 4) ----
        .equ RV, 0x7000
        .equ V_MAGIC, RV+0x00            ; word 'RS'
        .equ V_BOOT, RV+0x02             ; Hex girl boot 0/1 (also read by the page-1 boot player)
        .equ V_VER, RV+0x03              ; settings layout version (3 = v0.3)
        .equ V_LFO_WAVE, RV+0x04
        .equ V_LFO_RATE, RV+0x05
        .equ V_LFO_DEPTH, RV+0x06
        .equ V_LFO_BLOCK, RV+0x07        ; 0..5 = blocks A-F
        .equ V_LFO_SYNC, RV+0x08
        .equ V_BPM, RV+0x09
        .equ V_LFO_ON, RV+0x0A
        .equ V_LFO_PARAM, RV+0x0B        ; n-th editable parameter of that block
        .equ L_PHASE, RV+0x40            ; word, LFO state (src/lfo.asm)
        .equ L_RND, RV+0x42              ; word, random generator
        .equ L_LAST, RV+0x44             ; byte, program offset last modulated (0xFF = none)
        .equ L_DIV, RV+0x45
        .equ L_SCAN, RV+0x46             ; byte, scratch: numeric-param counter
        .equ NAMEBUF, RV+0x50            ; "F HALL" composed here
        .equ SETVER, 3
        .equ E_PAGE, RV+0x10             ; word: current page
        .equ E_SEL, RV+0x12
        .equ E_TOP, RV+0x13
        .equ E_EDIT, RV+0x14
        .equ E_DEPTH, RV+0x15
        .equ E_VCOL, RV+0x16             ; scratch: item index while drawing a row
        .equ E_STACK, RV+0x18            ; 4 x (page.w, sel.b, top.b)
        .equ NUMBUF, RV+0x30
        .equ MAGIC, 0x5253

; item layout (8 bytes): label.w, kind.b, max.b, ptr.w, aux.w
        .equ K_SUB, 0
        .equ K_NUM, 1
        .equ K_LIST, 2
        .equ K_ACT, 3
        .equ K_INFO, 4
        .equ K_DYN, 5                    ; ptr = RAM var, aux = routine: R1 = value -> R4 = text (EP set), R2 = max

        .org ROSETTA_BASE

; ================= entry / loop =================
ros_entry:
        LDC.B   #4,DP
        BSET.B  #0,@0x62E0               ; "not in PLAY", like the SYSTEM screens
        CMP.W   #MAGIC,@V_MAGIC
        BEQ     re0
        JSR     @ros_defaults
re0:    CMP.B   #SETVER,@V_VER           ; settings from an older Rosetta: add the new ones
        BEQ     re1
        JSR     @ros_defaults_v3
re1:    MOV.W   #pg_root,R0
        MOV.W   R0,@E_PAGE
        CLR.B   @E_SEL
        CLR.B   @E_TOP
        CLR.B   @E_EDIT
        CLR.B   @E_DEPTH
redraw_loop:
        JSR     @ros_draw
ros_loop:
        JSR     @ui_poll_a
        JSR     @ui_poll_b
        MOV.B   #12,R1                   ; dial -> slot 12 always (Sony routes it to the last A-F key's slot)
        LDC.B   #0,DP
        MOV.W   #ros_table,R0
        JMP     @ui_dispatch

        .align 2
ros_table:                               ; k0..k11, dial (word table: even address)
        .dw     ros_meter, k_a, k_b, k_c, k_d, k_e, k_f
        .dw     k_enter, k_exit, scr_system_select, scr_save, ros_bypass, k_dial

ros_meter:
        JSR     @key_meter_tune
        JMP     @redraw_loop
ros_bypass:
        JSR     @key_bypass
        JMP     @redraw_loop

ros_defaults:                            ; DP = 4
        MOV.W   #MAGIC,@V_MAGIC
        MOV.B   #1,@V_BOOT
        MOV.B   #120,@V_BPM
ros_defaults_v3:
        MOV.B   #0,@V_LFO_ON
        MOV.B   #0,@V_LFO_WAVE
        MOV.B   #40,@V_LFO_RATE
        MOV.B   #50,@V_LFO_DEPTH
        MOV.B   #5,@V_LFO_BLOCK
        MOV.B   #0,@V_LFO_PARAM
        MOV.B   #0,@V_LFO_SYNC
        MOV.B   #0xFF,@L_LAST
        MOV.B   #SETVER,@V_VER
        RTS

; ================= keys =================
k_a:    MOV.B   #0,R1
        BRA     k_row
k_b:    MOV.B   #1,R1
        BRA     k_row
k_c:    MOV.B   #2,R1
        BRA     k_row
k_d:    MOV.B   #3,R1
        BRA     k_row
k_e:    MOV.B   #4,R1
        BRA     k_row
k_f:    MOV.B   #5,R1
k_row:  LDC.B   #4,DP
        LDC.B   #0,EP
        MOV.W   @E_PAGE,R5
        MOV.B   @(4,R5),R2               ; item count
        CMP.B   R2,R1
        BCC     to_loop                  ; no such row
        CLR.B   @E_EDIT
        MOV.B   R1,@E_SEL
        JSR     @fix_top
        BRA     act
k_enter:
        LDC.B   #4,DP
        LDC.B   #0,EP
act:    JSR     @cur_item                ; R5 = item
        MOV.B   @(2,R5),R1               ; kind
        CMP.B   #K_SUB,R1
        BEQ     act_sub
        CMP.B   #K_ACT,R1
        BEQ     act_act
        CMP.B   #K_INFO,R1
        BEQ     to_loop
        BNOT.B  #0,@E_EDIT               ; NUM / LIST: edit on / off
        JMP     @redraw_loop
act_act:
        MOV.W   @(4,R5),R0
        JSR     @R0
        JMP     @redraw_loop
act_sub:
        MOV.B   @E_DEPTH,R1
        CMP.B   #4,R1
        BCC     to_loop                  ; too deep
        CLR.W   R0                       ; R0 = E_STACK + depth * 4
        MOV.B   R1,R0
        SHLL.W  R0
        SHLL.W  R0
        ADD.W   #E_STACK,R0
        MOV.W   @E_PAGE,R2
        MOV.W   R2,@R0
        MOV.B   @E_SEL,R2
        MOV.B   R2,@(2,R0)
        MOV.B   @E_TOP,R2
        MOV.B   R2,@(3,R0)
        ADD.B   #1,R1
        MOV.B   R1,@E_DEPTH
        MOV.W   @(4,R5),R2               ; target page
        MOV.W   R2,@E_PAGE
        CLR.B   @E_SEL
        CLR.B   @E_TOP
        CLR.B   @E_EDIT
        JMP     @redraw_loop
to_loop:
        JMP     @ros_loop

k_exit: LDC.B   #4,DP
        BTST.B  #0,@E_EDIT
        BEQ     ke1
        CLR.B   @E_EDIT
        JMP     @redraw_loop
ke1:    MOV.B   @E_DEPTH,R1
        BNE     ke2
        JMP     @scr_system_select       ; top level: back to SYSTEM
ke2:    SUB.B   #1,R1
        MOV.B   R1,@E_DEPTH
        CLR.W   R0
        MOV.B   R1,R0
        SHLL.W  R0
        SHLL.W  R0
        ADD.W   #E_STACK,R0
        MOV.W   @R0,R2
        MOV.W   R2,@E_PAGE
        MOV.B   @(2,R0),R2
        MOV.B   R2,@E_SEL
        MOV.B   @(3,R0),R2
        MOV.B   R2,@E_TOP
        JMP     @redraw_loop

k_dial: TST.W   R4                       ; R4 = dial delta (0 = idle)
        BEQ     to_loop
        LDC.B   #4,DP
        LDC.B   #0,EP
        BTST.B  #0,@E_EDIT
        BNE     dial_edit
        CLR.W   R2                       ; move the selection
        MOV.B   @E_SEL,R2
        ADD.W   R4,R2
        BPL     kd1
        CLR.W   R2
kd1:    MOV.W   @E_PAGE,R5
        CLR.W   R3
        MOV.B   @(4,R5),R3
        SUB.W   #1,R3                    ; R3 = last index
        CMP.W   R3,R2
        BLE     kd2
        MOV.W   R3,R2
kd2:    MOV.B   R2,@E_SEL
        JSR     @fix_top
        JMP     @redraw_loop
dial_edit:
        JSR     @cur_item
        MOV.W   @(4,R5),R0               ; RAM var
        CLR.W   R2
        MOV.B   @R0,R2
        ADD.W   R4,R2
        BPL     de1
        CLR.W   R2
de1:    CLR.W   R3
        MOV.B   @(2,R5),R1
        CMP.B   #K_DYN,R1
        BNE     de1b
        MOV.W   R2,@-SP                  ; K_DYN: ask the routine for max
        MOV.W   R0,@-SP
        MOV.W   R5,@-SP
        CLR.W   R1
        MOV.W   @(6,R5),R3
        JSR     @R3
        LDC.B   #0,EP
        MOV.W   R2,R3
        MOV.W   @SP+,R5
        MOV.W   @SP+,R0
        MOV.W   @SP+,R2
        BRA     de1c
de1b:   MOV.B   @(3,R5),R3               ; max
de1c:   CMP.W   R3,R2
        BLE     de2
        MOV.W   R3,R2
de2:    MOV.B   R2,@R0
        MOV.B   @(2,R5),R1
        CMP.B   #K_NUM,R1
        BNE     de3
        MOV.W   @(6,R5),R1               ; NUM apply routine (0 = none)
        BEQ     de3
        JSR     @R1
de3:    JMP     @redraw_loop

; ================= helpers =================
cur_item:                                ; R5 = current page + 6 + sel*8   (DP=4, EP=0)
        MOV.W   @E_PAGE,R5
        CLR.W   R1
        MOV.B   @E_SEL,R1
        SHLL.W  R1
        SHLL.W  R1
        SHLL.W  R1
        ADD.W   R1,R5
        ADD.W   #6,R5
        RTS

fix_top:                                 ; keep sel inside the 3 visible rows
        MOV.B   @E_SEL,R1
        MOV.B   @E_TOP,R2
        CMP.B   R2,R1
        BCC     ft1
        MOV.B   R1,@E_TOP                ; sel < top
        RTS
ft1:    ADD.B   #2,R2
        CMP.B   R2,R1
        BLS     ft2
        SUB.B   #2,R1                    ; sel > top + 2
        MOV.B   R1,@E_TOP
ft2:    RTS

strlen:                                  ; R4 = string (EP) -> R1 = length; keeps R4
        MOV.W   R4,@-SP
        CLR.W   R1
sl1:    TST.B   @R4+
        BEQ     sl2
        ADD.W   #1,R1
        BRA     sl1
sl2:    MOV.W   @SP+,R4
        RTS

; draw_str: R4 = string (current EP), R0 = gfx byte offset (DP=4), R3 = mode
;   mode 0: OR glyph   1: OR bold   2: clear bold (white on black)
; returns R0 after the last char. Clobbers R1, R2, R4, R5.
draw_str:
ds1:    MOV.B   @R4+,R1
        BEQ     ds9
        MOV.W   R4,@-SP
        STC.B   EP,R5                    ; keep the string's page
        MOV.W   R5,@-SP
        LDC.B   #0,EP
        CLR.W   R4                       ; R4 = FONT + char * 7
        MOV.B   R1,R4
        MOV.W   R4,R5
        SHLL.W  R4
        SHLL.W  R4
        SHLL.W  R4
        SUB.W   R5,R4
        ADD.W   #FONT,R4
        MOV.B   #7,R2
ds2:    MOV.B   @R4+,R5                  ; glyph row
        CMP.B   #0,R3
        BEQ     ds3
        MOV.B   R5,R1                    ; bold: g | g >> 1
        SHLR.B  R1
        OR.B    R1,R5
        AND.B   #0xFC,R5
        CMP.B   #2,R3
        BNE     ds3
        NOT.B   R5                       ; clear mode
        MOV.B   @R0,R1
        AND.B   R5,R1
        MOV.B   R1,@R0
        BRA     ds4
ds3:    MOV.B   @R0,R1
        OR.B    R5,R1
        MOV.B   R1,@R0
ds4:    ADD.W   #ROWB,R0
        SUB.B   #1,R2
        BNE     ds2
        SUB.W   #7*ROWB-1,R0             ; back to the glyph's top row, next column
        MOV.W   @SP+,R5
        LDC.B   R5,EP
        MOV.W   @SP+,R4
        BRA     ds1
ds9:    RTS

; draw_right: R4 = string, R0 = row start offset (col 0); right-aligned to end before col 35 (normal font).
draw_right:
        JSR     @strlen
        MOV.B   #35,R2
        SUB.B   R1,R2
        CLR.W   R1
        MOV.B   R2,R1
        ADD.W   R1,R0
        MOV.B   #0,R3
        JMP     @draw_str

; fill_bytes: R0 = offset, R1 = count, R2 = rows, R3 = byte   (DP=4)
fill_bytes:
fb1:    MOV.W   R0,@-SP
        MOV.W   R1,@-SP
fb2:    MOV.B   R3,@R0+
        SUB.W   #1,R1
        BNE     fb2
        MOV.W   @SP+,R1
        MOV.W   @SP+,R0
        ADD.W   #ROWB,R0
        SUB.B   #1,R2
        BNE     fb1
        RTS

; xor_bytes: R0 = offset, R1 = count, R2 = rows, R3 = mask
xor_bytes:
xb1:    MOV.W   R0,@-SP
        MOV.W   R1,@-SP
xb2:    MOV.B   @R0,R5
        XOR.B   R3,R5
        MOV.B   R5,@R0+
        SUB.W   #1,R1
        BNE     xb2
        MOV.W   @SP+,R1
        MOV.W   @SP+,R0
        ADD.W   #ROWB,R0
        SUB.B   #1,R2
        BNE     xb1
        RTS

; num_str: R1 = 0..255 -> NUMBUF (RAM, asciz, no leading zeros); R4 -> first digit (EP must be 4 to read it)
num_str:
        MOV.W   #NUMBUF+3,R0
        CLR.B   @R0
        CLR.W   R2
        MOV.B   R1,R2
ns1:    DIVXU.B #10,R2                   ; R2 = rem << 8 | quot
        MOV.W   R2,R3
        SWAP.B  R3
        ADD.B   #0x30,R3
        MOV.B   R3,@-R0
        AND.W   #0x00FF,R2
        BNE     ns1
        MOV.W   R0,R4
        RTS

; value_str: R5 = item (NUM / LIST) -> R4 = value text; EP = 4 for NUM (RAM digits), 0 for LIST
value_str:
        MOV.W   @(4,R5),R3               ; RAM var
        CLR.W   R1
        MOV.B   @R3,R1
        MOV.B   @(2,R5),R2
        CMP.B   #K_DYN,R2
        BNE     vs0
        MOV.W   @(6,R5),R3               ; K_DYN routine: R1 = value -> R4 = text, EP set
        JMP     @R3
vs0:    CMP.B   #K_LIST,R2
        BNE     vs1
        MOV.W   @(6,R5),R4               ; names table
        SHLL.W  R1
        ADD.W   R1,R4
        MOV.W   @R4,R4
        RTS
vs1:    JSR     @num_str
        LDC.B   #4,EP
        RTS

; ================= drawing =================
ros_draw:
        JSR     @draw_begin
        LDC.B   #4,DP
        LDC.B   #0,EP
        CLR.W   R0                       ; clear graphics + text layers
rd0:    CLR.B   @R0+
        CMP.W   #0x06AE,R0
        BNE     rd0
        MOV.B   #0xFC,R3                 ; frame: top, bottom, rule under the title
        MOV.W   #ROWB,R1                 ; (fill_bytes keeps R1 but uses up R2: reload it every call)
        MOV.B   #1,R2
        CLR.W   R0
        JSR     @fill_bytes
        MOV.B   #1,R2
        MOV.W   #39*ROWB,R0
        JSR     @fill_bytes
        MOV.B   #1,R2
        MOV.W   #10*ROWB,R0
        JSR     @fill_bytes
        MOV.W   #ROWB,R0                 ; sides
        MOV.B   #38,R2
rd1:    BSET.B  #7,@R0
        BSET.B  #2,@(37,R0)
        ADD.W   #ROWB,R0
        SUB.B   #1,R2
        BNE     rd1
        MOV.W   @E_PAGE,R5               ; tab: cols 0..len+1, rows 1..9
        MOV.W   @R5,R4
        JSR     @strlen
        ADD.W   #2,R1
        MOV.W   #ROWB,R0
        MOV.B   #9,R2
        MOV.B   #0xFC,R3
        JSR     @fill_bytes
        MOV.W   @E_PAGE,R5
        MOV.W   @R5,R4
        MOV.W   #2*ROWB+1,R0
        MOV.B   #2,R3
        JSR     @draw_str                ; bold white title
        MOV.W   @E_PAGE,R5               ; status, right
        MOV.W   @(2,R5),R4
        MOV.W   R4,R4
        BEQ     rd2
        MOV.W   #2*ROWB,R0
        JSR     @draw_right
rd2:    CLR.W   R6                       ; rows: R6 = visible row 0..2
rd3:    MOV.W   @E_PAGE,R5
        CLR.W   R1
        MOV.B   @E_TOP,R1
        ADD.W   R6,R1                    ; item index
        CLR.W   R2
        MOV.B   @(4,R5),R2
        CMP.W   R2,R1
        BCC     rd9
        MOV.W   R6,@-SP
        MOV.B   R1,@E_VCOL
        JSR     @draw_row
        MOV.W   @SP+,R6
        ADD.W   #1,R6
        CMP.W   #3,R6
        BNE     rd3
rd9:    JSR     @draw_select
        JSR     @draw_scroll
        JSR     @ui_after_draw
        JSR     @draw_end
        RTS

; draw_row: R6 = visible row, item index in E_VCOL
draw_row:
        MOV.W   R6,R1                    ; R0 = (12 + 9 * row) * 38
        MOV.W   R1,R2
        SHLL.W  R1
        SHLL.W  R1
        SHLL.W  R1
        ADD.W   R2,R1
        ADD.W   #12,R1
        MULXU.B #ROWB,R1
        MOV.W   R1,R0
        MOV.W   R0,@-SP                  ; row base
        MOV.W   @E_PAGE,R5
        CLR.W   R1
        MOV.B   @E_VCOL,R1
        SHLL.W  R1
        SHLL.W  R1
        SHLL.W  R1
        ADD.W   R1,R5
        ADD.W   #6,R5                    ; R5 = item
        MOV.W   R5,@-SP
        MOV.W   @R5,R4                   ; label
        ADD.W   #1,R0
        MOV.B   #0,R3
        JSR     @draw_str
        MOV.W   @SP+,R5
        MOV.W   @SP+,R0
        MOV.B   @(2,R5),R1               ; value by kind
        CMP.B   #K_SUB,R1
        BNE     dr1
        MOV.W   #s_gt,R4
        JMP     @draw_right
dr1:    CMP.B   #K_INFO,R1
        BNE     dr2
        MOV.W   @(6,R5),R4
        JMP     @draw_right
dr2:    CMP.B   #K_ACT,R1
        BNE     dr3
        MOV.W   #s_dots,R4
        JMP     @draw_right
dr3:    MOV.W   R0,@-SP
        JSR     @value_str
        MOV.W   @SP+,R0
        JSR     @draw_right
        LDC.B   #0,EP
        RTS

; selected row: invert cols 0..35 (keep the frame pixel); in edit mode punch the value back out
draw_select:
        CLR.W   R1
        MOV.B   @E_SEL,R1
        MOV.B   @E_TOP,R2
        SUB.B   R2,R1                    ; visible row
        MOV.W   R1,R2
        SHLL.W  R1
        SHLL.W  R1
        SHLL.W  R1
        ADD.W   R2,R1
        ADD.W   #11,R1
        MULXU.B #ROWB,R1
        MOV.W   R1,R0                    ; row y offset
        MOV.W   R0,@-SP
        MOV.B   #0x7C,R3
        MOV.W   #1,R1
        MOV.B   #9,R2
        JSR     @xor_bytes
        MOV.W   @SP+,R0
        MOV.W   R0,@-SP
        ADD.W   #1,R0
        MOV.B   #0xFC,R3
        MOV.W   #35,R1
        MOV.B   #9,R2
        JSR     @xor_bytes
        MOV.W   @SP+,R0
        BTST.B  #0,@E_EDIT
        BEQ     dsel9
        MOV.W   R0,@-SP
        JSR     @cur_item
        JSR     @value_str
        JSR     @strlen                  ; R1 = len
        LDC.B   #0,EP
        MOV.W   @SP+,R0
        MOV.B   #34,R2                   ; value box = cols (34 - len) .. 35
        SUB.B   R1,R2
        CLR.W   R3
        MOV.B   R2,R3
        ADD.W   R3,R0
        ADD.W   #2,R1                    ; width = len + 2
        MOV.B   #0xFC,R3
        MOV.B   #9,R2
        JSR     @xor_bytes
dsel9:  RTS

; scroll bar in col 36 when there are more than 3 items
draw_scroll:
        MOV.W   @E_PAGE,R5
        CLR.W   R1
        MOV.B   @(4,R5),R1
        CMP.W   #3,R1
        BLS     dsc9
        MOV.W   R1,@-SP                  ; count
        MOV.W   #11*ROWB+36,R0           ; track
        MOV.B   #0x84,R3
        MOV.W   #1,R1
        MOV.B   #28,R2
        JSR     @fill_bytes
        MOV.B   #0xFC,@11*ROWB+36
        MOV.B   #0xFC,@38*ROWB+36
        MOV.W   @SP+,R1                  ; thumb height = 78 / count (min 4)
        MOV.W   R1,@-SP
        MOV.W   #78,R2
        DIVXU.B R1,R2
        AND.W   #0x00FF,R2
        CMP.W   #4,R2
        BCC     dsc1
        MOV.W   #4,R2
dsc1:   MOV.W   @SP+,R1
        MOV.W   R2,@-SP                  ; th
        SUB.W   #3,R1                    ; count - 3
        MOV.W   #26,R3
        SUB.W   R2,R3                    ; 26 - th
        CLR.W   R2
        MOV.B   @E_TOP,R2
        MULXU.B R3,R2                    ; R2 = top * (26 - th)
        DIVXU.B R1,R2                    ; / (count - 3)
        AND.W   #0x00FF,R2
        ADD.W   #12,R2                   ; thumb y
        MULXU.B #ROWB,R2
        MOV.W   R2,R0
        ADD.W   #36,R0
        MOV.W   @SP+,R2                  ; rows = th
        MOV.W   #1,R1
        MOV.B   #0xFC,R3
        JSR     @fill_bytes
dsc9:   RTS

; ================= pages =================
s_gt:   .asciz  ">"
s_dots: .asciz  "..."
s_ver:  .asciz  "v0.3"
s_soon: .asciz  "SOON"
s_lfo1: .asciz  "LFO 1"

        .align 2
pg_root:
        .dw     t_root, s_ver
        .db     4, 0
        .dw     l_mod
        .db     K_SUB, 0
        .dw     pg_mod, 0
        .dw     l_tempo
        .db     K_SUB, 0
        .dw     pg_tempo, 0
        .dw     l_boot
        .db     K_SUB, 0
        .dw     pg_boot, 0
        .dw     l_about
        .db     K_SUB, 0
        .dw     pg_about, 0

pg_mod:
        .dw     t_mod, s_lfo1
        .db     6, 0
        .dw     l_lfo
        .db     K_LIST, 1
        .dw     V_LFO_ON, n_offon
        .dw     l_block
        .db     K_DYN, 5
        .dw     V_LFO_BLOCK, dyn_block
        .dw     l_param
        .db     K_DYN, 0
        .dw     V_LFO_PARAM, dyn_param
        .dw     l_wave
        .db     K_LIST, 4
        .dw     V_LFO_WAVE, n_waves
        .dw     l_rate
        .db     K_NUM, 99
        .dw     V_LFO_RATE, 0
        .dw     l_depth
        .db     K_NUM, 99
        .dw     V_LFO_DEPTH, 0

pg_tempo:
        .dw     t_tempo, s_soon
        .db     1, 0
        .dw     l_bpm
        .db     K_NUM, 250
        .dw     V_BPM, 0

pg_boot:
        .dw     t_boot, 0
        .db     1, 0
        .dw     l_hexgirl
        .db     K_LIST, 1
        .dw     V_BOOT, n_offon

pg_about:
        .dw     t_about, s_ver
        .db     4, 0
        .dw     l_os
        .db     K_INFO, 0
        .dw     0, v_os
        .dw     l_by
        .db     K_INFO, 0
        .dw     0, v_by
        .dw     l_mascot
        .db     K_INFO, 0
        .dw     0, v_mascot
        .dw     l_base
        .db     K_INFO, 0
        .dw     0, v_base

n_waves: .dw    w_tri, w_sine, w_square, w_saw, w_random
n_offon: .dw    o_off, o_on

t_root:   .asciz "ROSETTA OS"
t_mod:    .asciz "MODULATION"
t_tempo:  .asciz "TEMPO"
t_boot:   .asciz "BOOT"
t_about:  .asciz "ABOUT"
l_mod:    .asciz "MODULATION"
l_tempo:  .asciz "TEMPO"
l_boot:   .asciz "BOOT"
l_about:  .asciz "ABOUT"
l_wave:   .asciz "WAVE"
l_lfo:    .asciz "LFO"
l_block:  .asciz "BLOCK"
l_param:  .asciz "PARAM"
l_rate:   .asciz "RATE"
l_depth:  .asciz "DEPTH"
l_bpm:    .asciz "BPM"
l_hexgirl: .asciz "HEX GIRL"
l_os:     .asciz "OS"
l_by:     .asciz "BY"
l_mascot: .asciz "MASCOT"
l_base:   .asciz "BASE"
v_os:     .asciz "ROSETTA 0.3"
v_by:     .asciz "JSW + CLAUDE"
v_mascot: .asciz "HEX GIRL"
v_base:   .asciz "SONY 1.03E"
w_tri:    .asciz "TRIANGLE"
w_sine:   .asciz "SINE"
w_square: .asciz "SQUARE"
w_saw:    .asciz "SAW"
w_random: .asciz "RANDOM"
o_off:    .asciz "OFF"
o_on:     .asciz "ON"
