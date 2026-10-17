; ================= DDS-DSP SAMPLER (HR-MP5 build) =================
; Our own TMS57002 program (tools/keysdsp.py: 4 voices, one sample in the delay RAM, saw / pulse / triangle layers,
; envelopes, stereo pan, stereo resonant filter, THRU) owns the DSP while this page is open.
; Sony's TMS57002 writers are gated meanwhile (their page-1 entries jump to gate_* below); ours go past the gates
; through page-1 stubs (KST_CW / KST_PL / KST_CL). Leaving the page makes Sony's DSP-side copy of the program
; structure stale, so Sony's own program-change sequence reloads its TMS57002 program.
; Panel test: PLAY = hold the chord, NOTE / ROOT retune live. The sample is a 64-sample sine (fs / 64) until RECORD.

        .equ K_SET, RV+0x80              ; settings, in kh_tab order
        .equ K_PLAY, K_SET+0
        .equ K_NOTE, K_SET+1
        .equ K_CHORD, K_SET+2
        .equ K_SRC, K_SET+3
        .equ K_SMP, K_SET+4
        .equ K_SAW, K_SET+5
        .equ K_PUL, K_SET+6
        .equ K_PW, K_SET+7
        .equ K_TRI, K_SET+8
        .equ K_ATK, K_SET+9
        .equ K_DEC, K_SET+10
        .equ K_SUS, K_SET+11
        .equ K_REL, K_SET+12
        .equ K_CUT, K_SET+13
        .equ K_RES, K_SET+14
        .equ K_SPR, K_SET+15
        .equ K_THRU, K_SET+16
        .equ K_ROOT, K_SET+17
        .equ KS_N, 18
        .equ K_MAGIC, RV+0x92            ; 0x4B once the defaults are in
        .equ K_OWN, RV+0xA0              ; 1 = our program is in the TMS57002, Sony's writers are gated
        .equ K_LASTT, RV+0xA1            ; L_TICK at the last keys_tick
        .equ K_RECN, RV+0xA2             ; word: 5 ms ticks of recording left (0 = not recording)
        .equ K_VST, RV+0xA4              ; 4 bytes: voice state 0 off, 1 attack, 2 decay / sustain, 3 release
        .equ K_VT, RV+0xA8               ; 4 words: attack ticks left
        .equ K_SENT, RV+0xB0             ; KS_N bytes: value last sent (0xFF = send again)
        .equ K_TMP, RV+0xC4              ; word scratch
        .equ DSPCOPY, 0x1330             ; Sony's DSP-side copy of the program (page 4)
        .equ REC_TICKS, 300              ; 65536 samples = 1.49 s at 44.1 kHz

keys_defaults:                           ; DP = 4
        MOV.B   #0,@K_PLAY
        MOV.B   #77,@K_NOTE              ; F5 = the boot sine's own pitch (fs / 64 = 689 Hz)
        MOV.B   #0,@K_CHORD
        MOV.B   #0,@K_SRC
        MOV.B   #99,@K_SMP
        MOV.B   #0,@K_SAW
        MOV.B   #0,@K_PUL
        MOV.B   #50,@K_PW
        MOV.B   #0,@K_TRI
        MOV.B   #10,@K_ATK
        MOV.B   #40,@K_DEC
        MOV.B   #99,@K_SUS
        MOV.B   #40,@K_REL
        MOV.B   #99,@K_CUT
        MOV.B   #0,@K_RES
        MOV.B   #50,@K_SPR
        MOV.B   #99,@K_THRU
        MOV.B   #77,@K_ROOT
        MOV.B   #0x4B,@K_MAGIC
        RTS

; ---- redraw_loop: load / unload with the page, then send what changed ----
keys_apply:
        LDC.B   #4,DP
        CMP.B   #0x4B,@K_MAGIC
        BEQ     kap0
        JSR     @keys_defaults
kap0:   MOV.W   @E_PAGE,R0
        CMP.W   #pg_keys,R0
        BEQ     kap1
        TST.B   @K_OWN
        BEQ     kap9
        JMP     @keys_unload
kap1:   TST.B   @K_OWN
        BNE     kap2
        JSR     @keys_load
kap2:   CLR.W   R6
kap3:   MOV.W   R6,R0
        ADD.W   #K_SET,R0
        MOV.B   @R0,R1
        MOV.W   R6,R2
        ADD.W   #K_SENT,R2
        CMP.B   @R2,R1
        BEQ     kap4
        MOV.B   R1,@R2
        MOV.W   R6,@-SP
        MOV.W   R6,R4
        SHLL.W  R4
        ADD.W   #kh_tab,R4
        LDC.B   #0,EP
        MOV.W   @R4,R4
        JSR     @R4
        LDC.B   #4,DP
        MOV.W   @SP+,R6
kap4:   ADD.W   #1,R6
        CMP.W   #KS_N,R6
        BNE     kap3
kap9:   RTS

        .align 2
kh_tab: .dw     kh_play, kh_note, kh_chord, kh_src, kh_smp, kh_saw, kh_pul, kh_pw, kh_tri
        .dw     kh_none, kh_none, kh_sus, kh_rel, kh_filt, kh_filt, kh_spr, kh_thru, kh_note

; ---- helpers (DP = 4 in and out) ----
; kcw: R1 = coefficient address, R4:R5 = value. Interrupts off meanwhile (the tick talks to DSP #2 on the same port).
kcw:    STC.W   SR,R0
        MOV.W   R0,@-SP
        ORC.W   #0x0700,SR
        LDC.B   #0,DP
        PJSR    @KST_CW
        LDC.B   #4,DP
        MOV.W   @SP+,R0
        LDC.W   R0,SR
        RTS

; ksend_n: R2 = address list (page 0), R3 = count, R4:R5 = value -> every address
ksend_n:
kn1:    LDC.B   #0,DP
        MOV.B   @R2+,R1
        JSR     @kcw
        SUB.W   #1,R3
        BNE     kn1
        LDC.B   #4,DP
        RTS

; kvsend: R2 = per-voice address list, R6 = voice, R4:R5 = value
kvsend: LDC.B   #0,DP
        ADD.W   R6,R2
        MOV.B   @R2,R1
        JMP     @kcw

; lk32: R1 = index, R2 = table of 32-bit words (page 0) -> R4:R5
lk32:   CLR.W   R0
        MOV.B   R1,R0
        SHLL.W  R0
        SHLL.W  R0
        ADD.W   R0,R2
        LDC.B   #0,DP
        MOV.W   @R2,R4
        MOV.W   @(2,R2),R5
        LDC.B   #4,DP
        RTS

; lk_set: R0 = setting address, R2 = table -> R4:R5 = table[setting]
lk_set: CLR.W   R1
        MOV.B   @R0,R1
        JMP     @lk32

neg32:  NOT.W   R4                       ; R4:R5 = -R4:R5
        NOT.W   R5
        ADD.W   #1,R5
        BCC     ng1
        ADD.W   #1,R4
ng1:    RTS

kh_none: RTS

; ---- levels: one table value to 4 voice copies ----
kh_smp: MOV.W   #K_SMP,R0
        MOV.W   #KA_SL,R3
        BRA     klvl
kh_saw: MOV.W   #K_SAW,R0
        MOV.W   #KA_AL,R3
        BRA     klvl
kh_pul: MOV.W   #K_PUL,R0
        MOV.W   #KA_PL,R3
        BRA     klvl
kh_tri: MOV.W   #K_TRI,R0
        MOV.W   #KA_TL,R3
klvl:   MOV.W   R3,@K_TMP
        MOV.W   #kt_lvl,R2
        JSR     @lk_set
        MOV.W   @K_TMP,R2
        MOV.W   #4,R3
        JMP     @ksend_n

kh_pw:  MOV.W   #K_PW,R0                 ; PWT = 0.5 - lin  (0.5 .. -0.5, 50 = square)
        MOV.W   #kt_lin,R2
        JSR     @lk_set
        JSR     @neg32
        ADD.W   #0x4000,R4
        MOV.W   #KA_PWT,R2
        MOV.W   #4,R3
        JMP     @ksend_n

kh_thru:                                 ; MP5 mix: TH1 = lin (si1), TH0 = lin / 2 (si0)
        MOV.W   #K_THRU,R0
        MOV.W   #kt_lin,R2
        JSR     @lk_set
        MOV.W   #KA_TH1,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        SHAR.W  R4
        ROTXR.W R5
        MOV.W   #KA_TH0,R2
        MOV.W   #2,R3
        JMP     @ksend_n

kh_filt:                                 ; CUTOFF 99 = filter out (DRY), else WET low-pass at cut_tab
        CMP.B   #99,@K_CUT
        BNE     kf1
        CLR.W   R4
        CLR.W   R5
        MOV.W   #KA_WET,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        MOV.W   #0x7FFF,R4
        MOV.W   #0xFFFF,R5
        MOV.W   #KA_DRY,R2
        MOV.W   #2,R3
        JMP     @ksend_n
kf1:    MOV.W   #K_CUT,R0
        MOV.W   #kt_cut,R2
        JSR     @lk_set
        MOV.W   #KA_FF,R2                ; FF1 / FF2 = f
        MOV.W   #4,R3
        JSR     @ksend_n
        JSR     @neg32                   ; FMF = -f
        MOV.W   #KA_FMF,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        MOV.W   #K_RES,R0                ; FMQ = -q
        MOV.W   #kt_res,R2
        JSR     @lk_set
        JSR     @neg32
        MOV.W   #KA_FMQ,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        MOV.W   #0x7FFF,R4
        MOV.W   #0xFFFF,R5
        MOV.W   #KA_WET,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        CLR.W   R4
        CLR.W   R5
        MOV.W   #KA_DRY,R2
        MOV.W   #2,R3
        JMP     @ksend_n

kh_spr:                                  ; voice pans by chord size (kt_pan): centre, -s / +s, -s/2 / +s/2 (pan = 0.5 +- x)
        CLR.W   R6
ksp1:   JSR     @kvoices                 ; R1 = chord size
        MOV.W   R1,R2
        SUB.W   #1,R2
        SHLL.W  R2
        SHLL.W  R2
        ADD.W   R6,R2
        ADD.W   #kt_pan,R2
        LDC.B   #0,DP
        CLR.W   R3
        MOV.B   @R2,R3                   ; 0 centre, 1 -s, 2 +s, 3 -s/2, 4 +s/2
        LDC.B   #4,DP
        MOV.W   R3,@K_TMP
        MOV.W   #K_SPR,R0
        MOV.W   #kt_lin,R2
        JSR     @lk_set
        SHAR.W  R4                       ; x = s / 2 (full spread = hard L / R)
        ROTXR.W R5
        MOV.W   @K_TMP,R3
        BNE     ksp2
        CLR.W   R4                       ; centre
        CLR.W   R5
        BRA     ksp4
ksp2:   CMP.W   #3,R3
        BCS     ksp3
        SHAR.W  R4                       ; half spread
        ROTXR.W R5
ksp3:   BTST.B  #0,R3                    ; odd codes (1, 3) to the left: L = 0.5 + x
        BNE     ksp4
        JSR     @neg32                   ; even codes (2, 4) to the right: L = 0.5 - x
ksp4:   MOV.W   R4,@-SP
        MOV.W   R5,@-SP
        ADD.W   #0x4000,R4               ; L = 0.5 + x
        MOV.W   R4,@-SP
        MOV.W   R5,@-SP
        MOV.W   #KA_PANL,R2
        JSR     @kvsend
        MOV.W   @SP+,R5
        MOV.W   @SP+,R4
        NOT.W   R4                       ; R = 1.0 - L
        NOT.W   R5
        AND.W   #0x7FFF,R4
        MOV.W   #KA_PANR,R2
        JSR     @kvsend
        MOV.W   @SP+,R5
        MOV.W   @SP+,R4
        ADD.W   #1,R6
        CMP.W   #4,R6
        BNE     ksp1
        RTS

; ---- notes ----
; kvoices: R6 = voice -> R1 = chord size, R3 = semitone offset of voice R6 (signed byte)
kvoices:
        CLR.W   R2
        MOV.B   @K_CHORD,R2
        MOV.W   R2,R3                    ; *5
        SHLL.W  R2
        SHLL.W  R2
        ADD.W   R3,R2
        ADD.W   #kt_chord,R2
        LDC.B   #0,DP
        CLR.W   R1
        MOV.B   @R2,R1
        ADD.W   R6,R2
        MOV.B   @(1,R2),R3
        LDC.B   #4,DP
        EXTS.B  R3
        RTS

; kdl: R6 = voice -> R4:R5 = DL for NOTE - ROOT + offset (clamped to the table)
kdl:    JSR     @kvoices
        CLR.W   R1
        MOV.B   @K_NOTE,R1
        ADD.W   R3,R1
        CLR.W   R3
        MOV.B   @K_ROOT,R3
        SUB.W   R3,R1
        ADD.W   #64,R1
        BPL     kdl1
        CLR.W   R1
kdl1:    CMP.W   #127,R1
        BLE     kdl2
        MOV.W   #127,R1
kdl2:    MOV.W   #kt_note,R2
        JMP     @lk32

knote_on:                                ; R6 = voice
        JSR     @kdl
        MOV.W   #KA_DL,R2
        JSR     @kvsend
        CLR.W   R4
        CLR.W   R5
        MOV.W   #KA_PH,R2                ; restart at the sample start
        JSR     @kvsend
        MOV.W   #K_ATK,R0
        MOV.W   #kt_rate,R2
        JSR     @lk_set
        MOV.W   #KA_K,R2
        JSR     @kvsend
        MOV.W   #0x7FFF,R4
        MOV.W   #0xFFFF,R5
        MOV.W   #KA_T,R2
        JSR     @kvsend
        MOV.W   R6,R0
        ADD.W   #K_VST,R0
        MOV.B   #1,@R0
        CLR.W   R1                       ; attack ticks
        MOV.B   @K_ATK,R1
        SHLL.W  R1
        MOV.W   R1,R2
        ADD.W   #kt_atk,R2
        LDC.B   #0,DP
        MOV.W   @R2,R1
        LDC.B   #4,DP
        MOV.W   R6,R0
        SHLL.W  R0
        ADD.W   #K_VT,R0
        MOV.W   R1,@R0
        RTS

knote_off:                               ; R6 = voice
        MOV.W   R6,R0
        ADD.W   #K_VST,R0
        TST.B   @R0
        BEQ     kno9
        MOV.B   #3,@R0
        MOV.W   #K_REL,R0
        MOV.W   #kt_rate,R2
        JSR     @lk_set
        MOV.W   #KA_K,R2
        JSR     @kvsend
        CLR.W   R4
        CLR.W   R5
        MOV.W   #KA_T,R2
        JSR     @kvsend
kno9:   RTS

kh_chord:                                ; a new chord size re-spreads the voices, then (re)triggers
        JSR     @kh_spr
kh_play:                                 ; (re)trigger the chord, or release everything
        CLR.W   R6
kpl1:   TST.B   @K_PLAY
        BEQ     kpl3
        JSR     @kvoices
        CMP.W   R1,R6
        BCC     kpl3
        JSR     @knote_on
        BRA     kpl4
kpl3:   JSR     @knote_off
kpl4:   ADD.W   #1,R6
        CMP.W   #4,R6
        BNE     kpl1
        RTS

kh_note:                                 ; NOTE / ROOT: retune the sounding voices, no retrigger
        CLR.W   R6
knt1:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        MOV.B   @R0,R1
        BEQ     knt2
        CMP.B   #3,R1
        BEQ     knt2
        JSR     @kdl
        MOV.W   #KA_DL,R2
        JSR     @kvsend
knt2:   ADD.W   #1,R6
        CMP.W   #4,R6
        BNE     knt1
        RTS

kh_sus:                                  ; voices in sustain follow SUSTAIN at once
        CLR.W   R6
kss1:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        CMP.B   #2,@R0
        BNE     kss2
        MOV.W   #K_SUS,R0
        MOV.W   #kt_lvl,R2
        JSR     @lk_set
        MOV.W   #KA_T,R2
        JSR     @kvsend
kss2:   ADD.W   #1,R6
        CMP.W   #4,R6
        BNE     kss1
        RTS

kh_rel:                                  ; releasing voices follow RELEASE at once
        CLR.W   R6
krl1:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        CMP.B   #3,@R0
        BNE     krl2
        MOV.W   #K_REL,R0
        MOV.W   #kt_rate,R2
        JSR     @lk_set
        MOV.W   #KA_K,R2
        JSR     @kvsend
krl2:   ADD.W   #1,R6
        CMP.W   #4,R6
        BNE     krl1
        RTS

; ---- sample source / recording ----
kh_src: CLR.W   R4                       ; inputs off in the record mix
        CLR.W   R5
        MOV.W   #KA_G,R2
        MOV.W   #4,R3
        JSR     @ksend_n
        TST.B   @K_SRC
        BNE     ksr1
        MOV.W   #0x4000,R4               ; SINE: the 64-sample sine is recorded all the time
        MOV.W   #KA_GS,R1
        JSR     @kcw
        MOV.W   #0x7FFF,R4
        MOV.W   #0xFFFF,R5
        MOV.W   #KA_REC,R1
        JMP     @kcw
ksr1:   CLR.W   R4                       ; INPUT: keep the current sample until RECORD
        CLR.W   R5
        MOV.W   #KA_REC,R1
        JMP     @kcw

keys_record:                             ; K_ACT: 1.49 s from the selected input (SINE: nothing to do)
        LDC.B   #4,DP
        TST.B   @K_OWN
        BEQ     krc9
        TST.B   @K_SRC
        BEQ     krc9
        CLR.W   R4                       ; sine out of the mix
        CLR.W   R5
        MOV.W   #KA_GS,R1
        JSR     @kcw
        MOV.W   #0x4000,R4               ; the chosen pair at 0.5 each: A = si0 L + R, B = si1 L + R
        MOV.W   #KA_G,R2
        CMP.B   #1,@K_SRC
        BEQ     krc1
        ADD.W   #2,R2
krc1:   MOV.W   #2,R3
        JSR     @ksend_n
        CLR.W   R4                       ; AGE = 0: the sample starts now
        CLR.W   R5
        MOV.W   #KA_AMASK,R1
        JSR     @kcw
        MOV.W   #0xFFFF,R4
        MOV.W   #0xFFFF,R5
        MOV.W   #KA_AMASK,R1
        JSR     @kcw
        MOV.W   #0x7FFF,R4
        MOV.W   #KA_REC,R1
        JSR     @kcw
        MOV.W   #REC_TICKS,R0
        MOV.W   R0,@K_RECN
krc9:   RTS

; ---- from k_idle (every main-loop pass): envelope decay switch, end of recording ----
keys_tick:
        LDC.B   #4,DP
        TST.B   @K_OWN
        BEQ     ktk9
        MOV.B   @L_TICK,R0
        MOV.B   R0,R1
        SUB.B   @K_LASTT,R0              ; R0 = ticks since the last pass
        BEQ     ktk9
        MOV.B   R1,@K_LASTT
        CLR.W   R3
        MOV.B   R0,R3
        MOV.W   R3,@K_TMP
        MOV.W   @K_RECN,R1               ; recording
        BEQ     ktk2
        SUB.W   R3,R1
        BGT     ktk1
        CLR.W   R4
        CLR.W   R5
        MOV.W   R5,@K_RECN
        MOV.W   #KA_REC,R1
        JSR     @kcw
        BRA     ktk2
ktk1:   MOV.W   R1,@K_RECN
ktk2:   CLR.W   R6
ktk3:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        CMP.B   #1,@R0
        BNE     ktk5
        MOV.W   R6,R2
        SHLL.W  R2
        ADD.W   #K_VT,R2
        MOV.W   @R2,R1
        SUB.W   @K_TMP,R1
        MOV.W   R1,@R2
        BGT     ktk5
        MOV.B   #2,@R0                   ; attack over: decay to SUSTAIN
        MOV.W   #K_DEC,R0
        MOV.W   #kt_rate,R2
        JSR     @lk_set
        MOV.W   #KA_K,R2
        JSR     @kvsend
        MOV.W   #K_SUS,R0
        MOV.W   #kt_lvl,R2
        JSR     @lk_set
        MOV.W   #KA_T,R2
        JSR     @kvsend
ktk5:   ADD.W   #1,R6
        CMP.W   #4,R6
        BNE     ktk3
ktk9:   RTS

; ---- program in / out ----
keys_load:
        MOV.B   #1,@K_OWN                ; gate Sony first
        MOV.B   #0,@K_PLAY               ; start silent, with the sine as the sample
        MOV.B   #0,@K_SRC
        STC.W   SR,R0
        MOV.W   R0,@-SP
        ORC.W   #0x0700,SR
        MOV.W   R6,@-SP
        LDC.B   #0,DP
        LDC.B   #0,EP
        MOV.W   #keys_prog,R5            ; PLOAD: ST0, ST1, 256 words (PLOAD stays on: the DSP waits)
        MOV.W   #KEYS_PROG_LEN-1,R6
        PJSR    @KST_PL
        MOV.W   #keys_cmem,R5            ; + CLOAD: all 256 coefficients, then both released
        MOV.W   #KEYS_CMEM_LEN-1,R6
        PJSR    @KST_CL
kld_done:
        LDC.B   #4,DP
        MOV.W   @SP+,R6
        MOV.W   @SP+,R0
        LDC.W   R0,SR
        MOV.W   #K_SENT,R0               ; every setting goes out once
kld1:   MOV.B   #0xFF,@R0+
        CMP.W   #K_SENT+KS_N,R0
        BNE     kld1
        CLR.W   R0
        MOV.W   R0,@K_RECN
        MOV.W   R0,@K_VST
        MOV.W   R0,@K_VST+2
        MOV.B   @L_TICK,R0
        MOV.B   R0,@K_LASTT
        RTS

keys_unload:                             ; Sony's DSP-side program copy: flip the structure bytes (effect types),
        CLR.B   @K_OWN                   ; so Sony's program-change sequence reloads its TMS57002 program
        MOV.B   #0,@K_PLAY
        NOT.B   @DSPCOPY+0x39
        NOT.B   @DSPCOPY+0x4B
        NOT.B   @DSPCOPY+0x75
        NOT.B   @DSPCOPY+0x87
        RTS

; ---- gates: Sony's TMS57002 routine entries (MOV.B #4,@FD10) jump here; R0 is free in all of them ----
gate_cw:                                 ; 0x14755: R1 / R4:R5 coefficient write
        STC.B   DP,R0
        LDC.B   #4,DP
        TST.B   @K_OWN
        BNE     gcw1
        LDC.B   R0,DP
        MOV.B   #4,@0xFD10
        PJMP    @SONY_CW_CONT
gcw1:   LDC.B   R0,DP
        PJMP    @SONY_CW_SKIP
gate_cw2:                                ; 0x146DC: FE05 / FE06 / FE08 coefficient write
        STC.B   DP,R0
        LDC.B   #4,DP
        TST.B   @K_OWN
        BNE     gcx1
        LDC.B   R0,DP
        MOV.B   #4,@0xFD10
        PJMP    @SONY_CW2_CONT
gcx1:   LDC.B   R0,DP
        PJMP    @SONY_CW2_SKIP
gate_pl:                                 ; 0x1468B: program load
        STC.B   DP,R0
        LDC.B   #4,DP
        TST.B   @K_OWN
        BNE     gpl1
        LDC.B   R0,DP
        MOV.B   #4,@0xFD10
        PJMP    @SONY_PL_CONT
gpl1:   LDC.B   R0,DP
        PJMP    @SONY_LD_SKIP
gate_cl:                                 ; 0x14678: coefficient-memory load
        STC.B   DP,R0
        LDC.B   #4,DP
        TST.B   @K_OWN
        BNE     gcl1
        LDC.B   R0,DP
        MOV.B   #4,@0xFD10
        PJMP    @SONY_CL_CONT
gcl1:   LDC.B   R0,DP
        PJMP    @SONY_LD_SKIP
gate_mr:                                 ; 0x13E43: meter read (waits for host data our program may not give)
        STC.B   DP,R0
        LDC.B   #4,DP
        TST.B   @K_OWN
        BNE     gmr1
        LDC.B   R0,DP
        MOV.B   #4,@0xFD10
        PJMP    @SONY_MR_CONT
gmr1:   LDC.B   R0,DP
        PJMP    @SONY_MR_SKIP

; ---- the K_DYN note name: R1 = MIDI note -> "F#5" (C4 = 60), R2 = 127 ----
dyn_note:
        MOV.W   #NAMEBUF,R0
        CLR.W   R3
        MOV.B   R1,R3
        DIVXU.B #12,R3                   ; R3 = rem << 8 | quot (quot = octave + 1)
        MOV.W   R3,R2
        SWAP.B  R2
        AND.W   #0x00FF,R2
        SHLL.W  R2
        ADD.W   #kt_nn,R2
        LDC.B   #0,DP
        MOV.B   @R2+,R1
        MOV.B   @R2,R5
        LDC.B   #4,DP
        MOV.B   R1,@R0+
        CMP.B   #0x20,R5
        BEQ     dnn1
        MOV.B   R5,@R0+
dnn1:   AND.W   #0x00FF,R3
        BNE     dnn2
        MOV.B   #0x2D,@R0+               ; "-1"
        MOV.B   #0x31,@R0+
        BRA     dnn3
dnn2:   SUB.B   #1,R3
        ADD.B   #0x30,R3
        MOV.B   R3,@R0+
dnn3:   CLR.B   @R0
        MOV.W   #NAMEBUF,R4
        LDC.B   #4,EP
        MOV.W   #127,R2
        RTS

; ---- page ----
        .align 2
pg_keys:
        .dw     t_keys, s_keys
        .db     19, 0
        .dw     l_kplay
        .db     K_LIST, 1
        .dw     K_PLAY, n_offon
        .dw     l_knote
        .db     K_DYN, 0
        .dw     K_NOTE, dyn_note
        .dw     l_kchord
        .db     K_LIST, 5
        .dw     K_CHORD, n_kchord
        .dw     l_ksrc
        .db     K_LIST, 2
        .dw     K_SRC, n_ksrc
        .dw     l_krec
        .db     K_ACT, 0
        .dw     keys_record, 0
        .dw     l_ksmp
        .db     K_NUM, 99
        .dw     K_SMP, 0
        .dw     l_ksaw
        .db     K_NUM, 99
        .dw     K_SAW, 0
        .dw     l_kpul
        .db     K_NUM, 99
        .dw     K_PUL, 0
        .dw     l_kpw
        .db     K_NUM, 99
        .dw     K_PW, 0
        .dw     l_ktri
        .db     K_NUM, 99
        .dw     K_TRI, 0
        .dw     l_katk
        .db     K_NUM, 99
        .dw     K_ATK, 0
        .dw     l_kdec
        .db     K_NUM, 99
        .dw     K_DEC, 0
        .dw     l_ksus
        .db     K_NUM, 99
        .dw     K_SUS, 0
        .dw     l_krel
        .db     K_NUM, 99
        .dw     K_REL, 0
        .dw     l_kcut
        .db     K_NUM, 99
        .dw     K_CUT, 0
        .dw     l_kres
        .db     K_NUM, 99
        .dw     K_RES, 0
        .dw     l_kspr
        .db     K_NUM, 99
        .dw     K_SPR, 0
        .dw     l_kthru
        .db     K_NUM, 99
        .dw     K_THRU, 0
        .dw     l_kroot
        .db     K_DYN, 0
        .dw     K_ROOT, dyn_note

n_kchord: .dw   c_one, c_oct, c_fifth, c_maj, c_min, c_maj7
n_ksrc:   .dw   s_sine, s_ina, s_inb

t_keys:   .asciz "DDS-DSP SAMPLER"
l_keys:   .asciz "DDS-DSP SAMPLER"
s_keys:   .asciz "4 VOICE"
l_kplay:  .asciz "PLAY"
l_knote:  .asciz "NOTE"
l_kchord: .asciz "CHORD"
l_ksrc:   .asciz "SOURCE"
l_krec:   .asciz "RECORD"
l_ksmp:   .asciz "SAMPLE"
l_ksaw:   .asciz "SAW"
l_kpul:   .asciz "PULSE"
l_kpw:    .asciz "WIDTH"
l_ktri:   .asciz "TRIANGLE"
l_katk:   .asciz "ATTACK"
l_kdec:   .asciz "DECAY"
l_ksus:   .asciz "SUSTAIN"
l_krel:   .asciz "RELEASE"
l_kcut:   .asciz "CUTOFF"
l_kres:   .asciz "RESONANCE"
l_kspr:   .asciz "SPREAD"
l_kthru:  .asciz "THRU"
l_kroot:  .asciz "ROOT"
c_one:    .asciz "1 NOTE"
c_oct:    .asciz "OCTAVE"
c_fifth:  .asciz "FIFTH"
c_maj:    .asciz "MAJOR"
c_min:    .asciz "MINOR"
c_maj7:   .asciz "MAJ 7"
s_sine:   .asciz "SINE"
s_ina:    .asciz "INPUT A"
s_inb:    .asciz "INPUT B"
kt_nn:    .ascii "C C#D D#E F F#G G#A A#B "
kt_pan:   .db   0, 0, 0, 0                ; pan codes by chord size 1..4 (voice 0..3)
          .db   1, 2, 0, 0
          .db   0, 1, 2, 0
          .db   1, 2, 3, 4
kt_chord: .db   1, 0, 0, 0, 0
          .db   2, 0, 12, 0, 0
          .db   2, 0, 7, 0, 0
          .db   3, 0, 4, 7, 0
          .db   3, 0, 3, 7, 0
          .db   4, 0, 4, 7, 11
