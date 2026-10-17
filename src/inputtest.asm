; INPUT (ROSETTA -> INPUT): live meters for the AK5369's two channels + mute either one at the DSP input.
;
; Meters: the TMS57002 keeps peaks of the raw ADC samples (c(0a) = |si0_l|, c(0b) = |si0_r|); Sony's 5 ms tick
; reads them, maps them through a 0..24 table and leaves them at FD87 (L) / FD89 (R), peak hold FD86 / FD88.
; Sony's own METER popup shows one bar only. The page redraws the bars every ~40 ms from the idle dial slot.
;
; The TMS57002 reads both ADC channels with their own gain: c(08) = left, c(09) = right; Sony sets both to
; 0x14300000. Muting one tells whether the effect chain uses the other on its own (the second-input question).
; Sony's live-coefficient routine (page 1, 0x156B3: R1 = coefficient, R4:R5 = value) is reached through a
; 5-byte page-1 stub that rosetta_build.py places at CWRITE_P1 (JSR @0x56B3 ; PRTS).
; Not kept across power-offs: entering ROSETTA turns both inputs back on.

        .equ V_IN_L, RV+0x0C
        .equ V_IN_R, RV+0x0D
        .equ L_INSENT, RV+0x47           ; bit 0 / 1 = left / right muted in the DSP (what was last sent)
        .equ CWRITE_P1, 0x1F598
        .equ IN_GAIN_HI, 0x1430          ; Sony's c(08) / c(09) = 0x14300000

in_reset:                                ; DP = 4
        MOV.B   #1,@V_IN_L
        MOV.B   #1,@V_IN_R
        RTS

in_apply:                                ; send c(08) / c(09) when the setting differs from what was sent
        LDC.B   #4,DP
        CLR.W   R2
        TST.B   @V_IN_L
        BNE     ia1
        BSET.B  #0,R2
ia1:    TST.B   @V_IN_R
        BNE     ia2
        BSET.B  #1,R2
ia2:    CMP.B   @L_INSENT,R2
        BEQ     ia9
        MOV.B   R2,@L_INSENT
        MOV.W   R2,@-SP
        MOV.B   #8,R1
        CLR.W   R4
        BTST.B  #0,R2
        BNE     ia3
        MOV.W   #IN_GAIN_HI,R4
ia3:    JSR     @in_send
        MOV.W   @SP+,R2
        MOV.B   #9,R1
        CLR.W   R4
        BTST.B  #1,R2
        BNE     ia4
        MOV.W   #IN_GAIN_HI,R4
ia4:    JSR     @in_send
ia9:    RTS

in_send:                                 ; R1 = coefficient, R4 = high word (low word 0); interrupts off meanwhile,
        CLR.W   R5                       ; since the 5 ms tick also talks to the DSP
        STC.W   SR,R0
        MOV.W   R0,@-SP
        ORC.W   #0x0700,SR
        LDC.B   #0,DP
        PJSR    @CWRITE_P1
        LDC.B   #4,DP
        MOV.W   @SP+,R0
        LDC.W   R0,SR
        RTS

; draw_meter: R0 = row base (label top, col 0), R5 = K_METER item (EP = 0). DP = 4 on return.
; 24 segments in cols 9..32, rows +1..+5: filled 0xF0, empty = a dot on the bottom row, peak = a thin bar.
draw_meter:
        MOV.W   @(4,R5),R1
        MOV.W   @(6,R5),R2
        LDC.B   #0,DP                    ; meter bytes are in page 0 (FD80+ on-chip RAM)
        CLR.W   R3
        MOV.B   @R1,R3                   ; level
        CLR.W   R1
        MOV.B   @R2,R1                   ; peak
        LDC.B   #4,DP
        SUB.W   #1,R1                    ; peak segment index (0xFFFF = none)
        ADD.W   #ROWB+9,R0
        CLR.W   R2                       ; segment i
dm1:    MOV.B   #0xF0,R4
        MOV.B   R4,R5
        CMP.W   R3,R2
        BCS     dm3                      ; i < level: filled
        CLR.B   R4
        MOV.B   #0x80,R5                 ; empty: scale dot
        CMP.W   R1,R2
        BNE     dm3
        MOV.B   #0x60,R4                 ; peak hold
        MOV.B   R4,R5
dm3:    MOV.B   R4,@R0
        MOV.B   R4,@(ROWB,R0)
        MOV.B   R4,@(2*ROWB,R0)
        MOV.B   R4,@(3*ROWB,R0)
        MOV.B   R5,@(4*ROWB,R0)
        ADD.W   #1,R0
        ADD.W   #1,R2
        CMP.W   #24,R2
        BNE     dm1
        RTS

; k_idle: no key, no dial. On the INPUT page redraw the visible meter rows every 8 ticks (~40 ms).
k_idle:
        LDC.B   #4,DP
        LDC.B   #0,EP
        MOV.W   @E_PAGE,R5
        CMP.W   #pg_input,R5
        BNE     ki_out
        MOV.B   @L_TICK,R0
        SUB.B   @L_MTICK,R0
        CMP.B   #8,R0
        BCS     ki_out
        MOV.B   @L_TICK,R0
        MOV.B   R0,@L_MTICK
        JSR     @draw_begin
        LDC.B   #4,DP
        LDC.B   #0,EP
        CLR.W   R6                       ; visible row 0..2
ki1:    MOV.W   @E_PAGE,R5
        CLR.W   R1
        MOV.B   @E_TOP,R1
        ADD.W   R6,R1                    ; item index
        CLR.W   R2
        MOV.B   @(4,R5),R2
        CMP.W   R2,R1
        BCC     ki9
        SHLL.W  R1
        SHLL.W  R1
        SHLL.W  R1
        ADD.W   R1,R5
        ADD.W   #6,R5                    ; R5 = item
        MOV.B   @(2,R5),R1
        CMP.B   #K_METER,R1
        BNE     ki8
        MOV.W   R6,R1                    ; R0 = (12 + 9 * row) * 38, as draw_row
        SHLL.W  R1
        SHLL.W  R1
        SHLL.W  R1
        ADD.W   R6,R1
        ADD.W   #12,R1
        MULXU.B #ROWB,R1
        MOV.W   R1,R0
        MOV.W   R6,@-SP
        MOV.W   R0,@-SP
        JSR     @draw_meter
        MOV.W   @SP+,R0
        MOV.W   @SP+,R6
        CLR.W   R1                       ; selected row: invert the bar like draw_select does
        MOV.B   @E_SEL,R1
        MOV.B   @E_TOP,R2
        SUB.B   R2,R1
        CMP.W   R6,R1
        BNE     ki8
        ADD.W   #ROWB+9,R0
        MOV.W   #24,R1
        MOV.B   #5,R2
        MOV.B   #0xFC,R3
        MOV.W   R6,@-SP
        JSR     @xor_bytes
        MOV.W   @SP+,R6
ki8:    ADD.W   #1,R6
        CMP.W   #3,R6
        BNE     ki1
ki9:    JSR     @draw_end
ki_out: JMP     @ros_loop
