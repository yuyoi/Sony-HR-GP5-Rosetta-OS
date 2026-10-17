; INPUT (ROSETTA -> INPUT): meters for the input research.
;
; Signal path (service manual): AK5369 ADC -> IC502 (Sony CXD2705AQ DSP, SIA) -> SOA/SOB -> TMS57002 si0/si1.
; The TMS57002 never sees the raw ADC, so two kinds of meter:
;
; ADC L / ADC R: the raw analog channels, read by the CPU's own A/D on its spare inputs AN2 (P62, pin 47) / AN3
;   (P63, pin 48). Stock, both pins are tied to AVSS, so these bars stay flat until the wiring mod (lift the pins,
;   feed AINL / AINR through a cap into a 10k/10k bias to AVCC/AVSS). rosetta_build switches Sony's A/D scan from
;   AN0-AN1 to AN0-AN3 (ADCSR 0x31 -> 0x33 at boot), so pedal (AN1) and battery (AN0) readings stay as they were.
;   The idle loop samples AN2/AN3 on every pass, keeps min/max, and every 8 ticks turns peak-to-peak into
;   0..24 segments (2 dB each, thr_tab) with a falling peak hold.
;
; DSP A / DSP B: Sony's meter (FD87 / FD89). The TMS57002 peaks d(id) and d(id+1) with id = c(05) >> 24
;   (`lira`, word 26), packs them into c(07) and hands that to the host (`lpc`). SOURCE re-aims c(05):
;   SI0 = d(1F)/d(20) = si0_l / si0_r, SI1 L = d(21)/d(22) = si1_l twice (Sony's own setting), SI1 R = d(27)/d(28)
;   = si1_r (the 38-tap FIR input) and its delayed copy. B reads 36 dB more sensitive than A. c(05) is used only
;   by the meter code. Entering ROSETTA puts Sony's setting back.
; Coefficient writes go through Sony's live-coefficient routine (page 1, 0x156B3: R1 = coefficient, R4:R5 = value)
; via a 5-byte page-1 stub that rosetta_build.py places at CWRITE_P1 (JSR @0x56B3 ; PRTS).

        .equ V_MSRC, RV+0x0C             ; DSP meter source 0..2
        .equ L_SRCSENT, RV+0x47          ; source last sent to c(05) (0xFF = force)
        .equ M_ADL, RV+0x60              ; ADC L bar 0..24
        .equ M_ADR, RV+0x61
        .equ M_PKL, RV+0x62              ; peak holds
        .equ M_PKR, RV+0x63
        .equ M_MINL, RV+0x64             ; min / max since the last refresh
        .equ M_MAXL, RV+0x65
        .equ M_MINR, RV+0x66
        .equ M_MAXR, RV+0x67
        .equ CWRITE_P1, 0x1F598
        .equ AD_AN2, 0xFFE4              ; ADDRC high byte
        .equ AD_AN3, 0xFFE6              ; ADDRD high byte

in_reset:                                ; ros_entry, DP = 4
        MOV.B   #1,@V_MSRC               ; Sony's own meter source
        MOV.B   #0xFF,@L_SRCSENT         ; and send it once
        MOV.B   #0xFF,@M_MINL
        CLR.B   @M_MAXL
        MOV.B   #0xFF,@M_MINR
        CLR.B   @M_MAXR
        CLR.B   @M_PKL
        CLR.B   @M_PKR
        RTS

in_apply:                                ; send c(05) when SOURCE differs from what was sent
        LDC.B   #4,DP
        LDC.B   #0,EP
        CLR.W   R2
        MOV.B   @V_MSRC,R2
        CMP.B   @L_SRCSENT,R2
        BEQ     ia9
        MOV.B   R2,@L_SRCSENT
        MOV.W   R2,R4
        ADD.W   #src_tab,R4
        CLR.W   R0
        MOV.B   @R4,R0                   ; data address
        SWAP.B  R0                       ; value = addr << 24: high word = addr << 8
        MOV.W   R0,R4
        MOV.B   #5,R1
        JSR     @in_send
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

; draw_meter: R0 = row base (label top, col 0), R5 = K_METER item (EP = 0). Level / peak bytes live in page 0
; (Sony's FDxx) or in Rosetta RAM (page 4). DP = 4 on return.
; 24 segments in cols 9..32, rows +1..+5: filled 0xF0, empty = a dot on the bottom row, peak = a thin bar.
draw_meter:
        MOV.W   @(4,R5),R1
        MOV.W   @(6,R5),R2
        LDC.B   #4,DP
        CMP.W   #0xF000,R1
        BCS     dm0
        LDC.B   #0,DP                    ; FD80+ on-chip RAM is page 0
dm0:    CLR.W   R3
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

; adc_bar: R0 = peak-to-peak 0..255 -> R1 = segments 0..24 (thresholds 2 dB apart)
adc_bar:
        CLR.W   R1
        MOV.W   #thr_tab,R4
        MOV.B   #24,R2
ab1:    CMP.B   @R4+,R0
        BCS     ab9
        ADD.B   #1,R1
        SUB.B   #1,R2
        BNE     ab1
ab9:    RTS

; adc_level: R3 = M_MINx address -> min/max -> segments, falling peak hold, min/max reset. DP = 4, EP = 0.
adc_level:
        MOV.B   @(1,R3),R0               ; max - min
        SUB.B   @R3,R0
        BCC     al1
        CLR.B   R0                       ; no samples yet
al1:    MOV.W   R3,@-SP
        JSR     @adc_bar
        MOV.W   @SP+,R3
        MOV.B   #0xFF,@R3                ; reset min / max
        CLR.B   @(1,R3)
        RTS                              ; R1 = bar

; k_idle: no key, no dial. On the INPUT page sample AN2/AN3 every pass and redraw the meters every 8 ticks.
k_idle:
        LDC.B   #4,DP
        LDC.B   #0,EP
        MOV.W   @E_PAGE,R5
        CMP.W   #pg_input,R5
        BEQ     ki0
        JMP     @ros_loop
ki0:    LDC.B   #0,DP
        MOV.B   @AD_AN2,R2
        MOV.B   @AD_AN3,R3
        LDC.B   #4,DP
        CMP.B   @M_MINL,R2
        BCC     ks1
        MOV.B   R2,@M_MINL
ks1:    CMP.B   @M_MAXL,R2
        BLS     ks2
        MOV.B   R2,@M_MAXL
ks2:    CMP.B   @M_MINR,R3
        BCC     ks3
        MOV.B   R3,@M_MINR
ks3:    CMP.B   @M_MAXR,R3
        BLS     ks4
        MOV.B   R3,@M_MAXR
ks4:    MOV.B   @L_TICK,R0
        SUB.B   @L_MTICK,R0
        CMP.B   #8,R0
        BCC     ki_go
        JMP     @ros_loop
ki_go:  MOV.B   @L_TICK,R0
        MOV.B   R0,@L_MTICK
        MOV.W   #M_MINL,R3               ; ADC L: level + falling peak
        JSR     @adc_level
        MOV.B   R1,@M_ADL
        MOV.W   #M_PKL,R3
        JSR     @pk_fall
        MOV.W   #M_MINR,R3               ; ADC R
        JSR     @adc_level
        MOV.B   R1,@M_ADR
        MOV.W   #M_PKR,R3
        JSR     @pk_fall
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
        JMP     @ros_loop

pk_fall:                                 ; R3 = peak byte, R1 = new level: peak = max(level, peak - 1)
        MOV.B   @R3,R0
        BEQ     pkf1
        SUB.B   #1,R0
pkf1:   CMP.B   R0,R1
        BLS     pkf2
        MOV.B   R1,R0
pkf2:   MOV.B   R0,@R3
        RTS

src_tab: .db    0x1F, 0x21, 0x27         ; c(05) >> 24 for SOURCE SI0 / SI1 L / SI1 R
