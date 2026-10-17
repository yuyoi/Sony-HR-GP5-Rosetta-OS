; NOT IN THE BUILD since 0.3.2 (done: the glass is exactly 228 x 40). To use again: append it in rosetta_build.py,
; add a K_ACT root item pointing at edge_test and an l_edge "EDGE TEST" string.
;
; EDGE TEST (ROSETTA menu): does the LCD glass have pixels outside Sony's 228 x 40 picture?
;
; Sony's init (0x0F58) sets the SED1335 to 38 bytes x 6 dots = 228 dots, 40 lines, dot scroll 3.
; Extra glass can only sit right of / below that picture (the drive chain starts at the top left).
; 1. 252 x 48 (C/R 42, L/F 48), every dot black, ~3 s, then blink: driven glass flashes, dead glass doesn't.
; 2. Sony's geometry with a black frame + "EDGE TEST": a preview of a frame mod, ~6 s.
; 3. Reboot (reset entry), which restores the LCD, CGRAM and DSPs.
; Interrupts stay off the whole time (no LCD streamer, no key scan, no LFO). Timing is estimated (no timer).

        .equ LCD_D, 0xFD30               ; SED1335 parameter / data
        .equ LCD_C, 0xFD31               ; SED1335 command
        .equ SONY_LCD_INIT, 0x0F58       ; SYSTEM SET, SCROLL, HDOT, OVLAY, CSRFORM, CSRW 0, DISP OFF
        .equ SONY_LCD_ON, 0x0FE8         ; DISP ON (both layers)
        .equ RESET_ENTRY, 0x0181

edge_test:
        ORC.W   #0x0700,SR               ; interrupts off: Sony's LCD streamer would overwrite VRAM
        LDC.B   #0,DP
        LDC.B   #0,EP
        MOV.W   #et_big,R4
        JSR     @lcd_seq
        MOV.B   #0x42,@LCD_C             ; MWRITE: all 2 KB of VRAM black
        MOV.B   #0xFF,R0
        MOV.W   #0x800,R1
        JSR     @lcd_fill
        MOV.B   #0x59,@LCD_C             ; DISP ON, layer 1 only
        MOV.B   #0x04,@LCD_D
        MOV.W   #30,R3
        JSR     @et_delay
        MOV.W   #10,R5                   ; 10 blinks
et_b1:  MOV.B   #0x58,@LCD_C
        MOV.B   #0x04,@LCD_D
        MOV.W   #6,R3
        JSR     @et_delay
        MOV.B   #0x59,@LCD_C
        MOV.B   #0x04,@LCD_D
        MOV.W   #6,R3
        JSR     @et_delay
        SUB.W   #1,R5
        BNE     et_b1

        JSR     @SONY_LCD_INIT           ; back to 228 x 40, dot scroll 3, display off
        LDC.B   #0,DP
        MOV.B   #0x4C,@LCD_C             ; CSRDIR right
        MOV.B   #0x46,@LCD_C             ; CSRW 0
        MOV.B   #0,@LCD_D
        MOV.B   #0,@LCD_D
        MOV.B   #0x42,@LCD_C             ; MWRITE graphics layer: rows 0-1 and 38-39 black, columns 0 and 37 black
        CLR.W   R2
et_g1:  CMP.W   #2,R2
        BCS     et_full
        CMP.W   #38,R2
        BCC     et_full
        MOV.B   #0xFC,@LCD_D
        CLR.B   R0
        MOV.W   #36,R1
        JSR     @lcd_fill
        MOV.B   #0xFC,@LCD_D
        BRA     et_g2
et_full:
        MOV.B   #0xFC,R0
        MOV.W   #38,R1
        JSR     @lcd_fill
et_g2:  ADD.W   #1,R2
        CMP.W   #40,R2
        BNE     et_g1
        MOV.B   #0x20,R0                 ; text layer (cursor is now at 0x5F0): "EDGE TEST" on row 2
        MOV.W   #2*38+14,R1
        JSR     @lcd_fill
        MOV.W   #l_edge,R4
et_t1:  MOV.B   @R4+,R0
        BEQ     et_t2
        MOV.B   R0,@LCD_D
        BRA     et_t1
et_t2:  MOV.B   #0x20,R0
        MOV.W   #5*38-2*38-14-9,R1
        JSR     @lcd_fill
        JSR     @SONY_LCD_ON
        MOV.W   #60,R3
        JSR     @et_delay
        JMP     @RESET_ENTRY

lcd_seq:                                 ; R4 = {cmd, n, n params} ... 0 -> SED1335   (DP = 0, EP = 0)
ls1:    MOV.B   @R4+,R0
        BEQ     ls9
        MOV.B   R0,@LCD_C
        MOV.B   @R4+,R1
        BEQ     ls1
ls2:    MOV.B   @R4+,R0
        MOV.B   R0,@LCD_D
        SUB.B   #1,R1
        BNE     ls2
        BRA     ls1
ls9:    RTS

lcd_fill:                                ; R1 x byte R0 to the SED1335 (after MWRITE), DP = 0
lf1:    MOV.B   R0,@LCD_D
        SUB.W   #1,R1
        BNE     lf1
        RTS

et_delay:                                ; R3 x ~0.1 s (65536 loops, estimated)
ed1:    CLR.W   R2
ed2:    SUB.W   #1,R2
        BNE     ed2
        SUB.W   #1,R3
        BNE     ed1
        RTS

et_big:
        .db     0x58, 1, 0x00                                   ; DISP OFF
        .db     0x40, 8, 0x30, 0x85, 0x07, 41, 0x80, 47, 42, 0  ; SYSTEM SET: 42 B x 6 = 252 dots, 48 lines, AP 42
        .db     0x44, 6, 0, 0, 48, 0, 0, 48                     ; SCROLL: both blocks at VRAM 0
        .db     0x5A, 1, 0                                      ; HDOT SCR 0
        .db     0x5B, 1, 0x04                                   ; OVLAY: layer 1 = graphics, OR
        .db     0x4C, 0                                         ; CSRDIR right
        .db     0x46, 2, 0, 0                                   ; CSRW 0
        .db     0
