; INPUT TEST (ROSETTA -> INPUT): mute the AK5369's left or right channel at the DSP input.
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
