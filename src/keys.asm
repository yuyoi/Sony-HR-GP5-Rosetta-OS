; ================= DDS-DSP SAMPLER (HR-MP5 build) =================
; Our own TMS57002 program (tools/keysdsp.py: 8 voices playing the delay RAM, which holds a 64-sample wavetable from
; one DDS oscillator (SYNTH) or a recording of the input (SAMPLE); envelopes, L / R voice groups with crossfeed,
; stereo resonant filter, THRU) owns the DSP while this page is open.
; Sony's TMS57002 writers are gated meanwhile (their page-1 entries jump to gate_* below); ours go past the gates
; through page-1 stubs (KST_CW / KST_PL / KST_CL). Leaving the page makes Sony's DSP-side copy of the program
; structure stale, so Sony's own program-change sequence reloads its TMS57002 program.
; Panel test: PLAY = hold the chord, NOTE / ROOT retune live. At load: SYNTH with a sine.
; MIDI IN (midi_rx / keys_midi): notes on all 16 channels take voices (a CHORD per key, oldest voice stolen), velocity,
; sustain pedal (CC 64), all notes off (CC 120 / 123).

        .equ K_SET, RV+0x80              ; settings, in kh_tab order
        .equ K_PLAY, K_SET+0
        .equ K_NOTE, K_SET+1
        .equ K_CHORD, K_SET+2
        .equ K_MODE, K_SET+3             ; 0 SYNTH, 1 SAMPLE
        .equ K_SINE, K_SET+4
        .equ K_SAW, K_SET+5
        .equ K_PUL, K_SET+6
        .equ K_PW, K_SET+7
        .equ K_SRC, K_SET+8              ; 0 INPUT A (si0 L), 1 INPUT B (si1 L)
        .equ K_ATK, K_SET+9
        .equ K_DEC, K_SET+10
        .equ K_SUS, K_SET+11
        .equ K_REL, K_SET+12
        .equ K_CUT, K_SET+13
        .equ K_RES, K_SET+14
        .equ K_SPR, K_SET+15
        .equ K_VOL, K_SET+16
        .equ K_THRU, K_SET+17
        .equ K_ROOT, K_SET+18
        .equ KS_N, 19
        .equ K_MAGIC, RV+0x93            ; 0x4C once the defaults are in (layout 2)
        .equ K_OWN, RV+0xA0              ; 1 = our program is in the TMS57002, Sony's writers are gated
        .equ K_LASTT, RV+0xA1            ; L_TICK at the last keys_tick
        .equ K_RECN, RV+0xA2             ; word: 5 ms ticks of recording left (0 = not recording)
        .equ K_VST, RV+0xA4              ; 8 bytes: voice state 0 off, 1 attack, 2 decay / sustain, 3 release
        .equ K_VT, RV+0xAC               ; 8 words: attack ticks left
        .equ K_SENT, RV+0xBC             ; KS_N bytes: value last sent (0xFF = send again)
        .equ K_TMP, RV+0xD0              ; 2 words scratch
        .equ K_VK, RV+0x68               ; 8 bytes: MIDI key that owns each voice (0xFF panel / none, 0xFD held by the pedal)
        .equ K_VN, RV+0x70               ; 8 bytes: the note each voice plays (60 = C4)
        .equ K_VAGE, RV+0x78             ; 8 bytes: K_SEQ when the voice was taken (stealing: oldest first)
        .equ K_VVEL, RV+0xD4             ; 8 bytes: velocity (panel 127)
        .equ K_MST, RV+0x94              ; MIDI IN parser (midi_rx, in the interrupt): running status (0 = none)
        .equ K_MD1, RV+0x95              ;   first data byte (0xFF = none yet)
        .equ K_MQW, RV+0x96              ;   event queue write index (midi_rx)
        .equ K_MQR, RV+0x97              ;   read index (keys_midi)
        .equ K_SEQ, RV+0x98              ; voice allocation counter
        .equ K_PED, RV+0x99              ; sustain pedal down
        .equ K_MKEY, RV+0xDC             ; keys_midi: the event's key, value; chord tone, its note
        .equ K_MVEL, RV+0xDD
        .equ K_MJ, RV+0xDE
        .equ K_MN, RV+0xDF
        .equ K_MQ, RV+0xE0               ; 16 events x (key, value): 0-127 note (value = velocity, 0 = off),
                                         ; 0x80 sustain pedal (value = CC 64), 0x81 all notes off, 0xA0 + n setting n
        .equ K_GO, RV+0x9A               ; CC 119 -> keys_tick: 1 = open this page, 2 = leave it
        .equ K_REDRAW, RV+0x9B           ; keys_tick -> k_idle: go through redraw_loop (page change, CC settings)
        .equ K_SX, RV+0x9C               ; SysEx (midi_rx): 0 none, 1 manufacturer next, 4 'R' next, 2 ours, 3 not ours
        .equ K_SXN, RV+0x9D              ;   bytes of ours so far
        .equ K_SXCMD, RV+0x9E            ;   a command for keys_tick (0 = none)
        .equ K_LSRC, RV+0x9F             ; keys_load: 0 = the ROM image, 1 = K_BUF (the link's)
        .equ K_SXB, 0x7100               ; SysEx staging, 12 bytes: command, arguments (0x47100-: unused by Sony)
        .equ K_SXOFF, 0x710C             ; word: next K_BUF offset (command 10)
        .equ K_SXHI, 0x710E              ; high nibble waiting (0xFF = none)
        .equ K_BUF, 0x7110               ; program + coefficient image from the link (KEYS_PROG_LEN + KEYS_CMEM_LEN)
        .equ DSPCOPY, 0x1330             ; Sony's DSP-side copy of the program (page 4)
        .equ REC_TICKS, 300              ; 65536 samples = 1.49 s at 44.1 kHz
        .equ LVLMAX, 0x4000              ; a voice's full level (0.5: 4 voices per side without clipping)

keys_defaults:                           ; DP = 4
        MOV.B   #0,@K_PLAY
        MOV.B   #77,@K_NOTE              ; F5 = the wavetable's own pitch (fs / 64 = 689 Hz)
        MOV.B   #0,@K_CHORD
        MOV.B   #0,@K_MODE
        MOV.B   #99,@K_SINE
        MOV.B   #0,@K_SAW
        MOV.B   #0,@K_PUL
        MOV.B   #50,@K_PW
        MOV.B   #1,@K_SRC
        MOV.B   #10,@K_ATK
        MOV.B   #40,@K_DEC
        MOV.B   #99,@K_SUS
        MOV.B   #40,@K_REL
        MOV.B   #99,@K_CUT
        MOV.B   #0,@K_RES
        MOV.B   #50,@K_SPR
        MOV.B   #99,@K_VOL
        MOV.B   #99,@K_THRU
        MOV.B   #77,@K_ROOT
        MOV.B   #0x4C,@K_MAGIC
        RTS

; ---- redraw_loop: load / unload with the page, then send what changed ----
keys_apply:
        LDC.B   #4,DP
        CMP.B   #0x4C,@K_MAGIC
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
        CLR.B   @K_LSRC
        CLR.B   @K_SXCMD
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
kh_tab: .dw     kh_play, kh_note, kh_chord, kh_mode, kh_wave, kh_wave, kh_wave, kh_pw, kh_none
        .dw     kh_none, kh_none, kh_sus, kh_rel, kh_filt, kh_filt, kh_vol, kh_vol, kh_thru, kh_note

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

; mulq: R4 = Q15 (hi word of a Q31), R3 = Q15 -> R4:R5 = their product as Q31
mulq:   MULXU.W R3,R4
        SHLL.W  R5
        ROTXL.W R4
        RTS

kh_none: RTS

; ---- the wavetable mix (SYNTH) and the record mix ----
kh_wave:                                 ; SYNTH: RGS / RGW / RGP = level / 2; SAMPLE: all 0
        MOV.W   #KA_RGWAVE,R2
        MOV.W   R2,@K_TMP
        MOV.W   #K_SINE,R0
kw1:    MOV.W   R0,@K_TMP+2
        CLR.W   R4
        CLR.W   R5
        TST.B   @K_MODE
        BNE     kw2
        MOV.W   #kt_lvl,R2
        JSR     @lk_set
        SHAR.W  R4
        ROTXR.W R5
kw2:    MOV.W   @K_TMP,R2
        LDC.B   #0,DP
        MOV.B   @R2+,R1
        LDC.B   #4,DP
        MOV.W   R2,@K_TMP
        JSR     @kcw
        MOV.W   @K_TMP+2,R0
        ADD.W   #1,R0
        CMP.W   #K_PUL+1,R0
        BNE     kw1
        RTS

kh_mode:                                 ; SYNTH: voices read the last 64 samples, the oscillator writes all the time
        MOV.W   #0x003F,R4               ; SAMPLE: voices read the whole RAM, nothing is written until RECORD
        MOV.W   #0xFFFF,R5
        TST.B   @K_MODE
        BEQ     kmd1
        MOV.W   #0xFFFF,R4
kmd1:   MOV.W   #KA_XM,R2
        MOV.W   #KNV,R3
        JSR     @ksend_n
        CLR.W   R4                       ; inputs out of the record mix
        CLR.W   R5
        MOV.W   #KA_RGIN,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        CLR.W   R0
        MOV.W   R0,@K_RECN
        MOV.W   #0x8000,R4               ; MREC = -1 (SYNTH) or 0 (SAMPLE: the RAM stays)
        TST.B   @K_MODE
        BEQ     kmd2
        CLR.W   R4
kmd2:   CLR.W   R5
        MOV.W   #KA_MREC,R1
        JSR     @kcw
        JMP     @kh_wave

kh_pw:  MOV.W   #K_PW,R0                 ; PWT = 0.5 - lin  (0.5 .. -0.5, 50 = square)
        MOV.W   #kt_lin,R2
        JSR     @lk_set
        JSR     @neg32
        ADD.W   #0x4000,R4
        MOV.W   #KA_PWT,R1
        JMP     @kcw

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

kh_filt:                                 ; f = cut_tab (30 Hz .. 7 kHz), q = res_tab (1.0 .. 0.08)
        MOV.W   #K_CUT,R0
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
        JMP     @ksend_n

kh_vol:                                  ; VIN = volume, VXF = volume * (1 - spread)  (SPREAD 0 = mono)
        MOV.W   #K_VOL,R0
        MOV.W   #kt_lin,R2
        JSR     @lk_set
        MOV.W   R4,@K_TMP
        MOV.W   #KA_VIN,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        MOV.W   #K_SPR,R0
        MOV.W   #kt_lin,R2
        JSR     @lk_set
        NOT.W   R4                       ; 1 - spread (hi word)
        AND.W   #0x7FFF,R4
        MOV.W   R4,R3
        MOV.W   @K_TMP,R4
        JSR     @mulq
        MOV.W   #KA_VXF,R2
        MOV.W   #2,R3
        JMP     @ksend_n

; ---- envelopes: LVL = KD LVL + KT with KD = 1 - K, KT = K T ----
; kenv: R6 = voice, R0 = rate setting address, R3 = target (Q15) -> KD, KT out
kenv:   MOV.W   R3,@K_TMP+2
        MOV.W   #kt_rate,R2
        JSR     @lk_set
        MOV.W   R4,@K_TMP                ; K hi
        NOT.W   R4                       ; KD = 0x7FFFFFFF - K
        NOT.W   R5
        AND.W   #0x7FFF,R4
        MOV.W   #KA_KD,R2
        JSR     @kvsend
        MOV.W   @K_TMP,R4
        MOV.W   @K_TMP+2,R3
        JSR     @mulq
        MOV.W   #KA_KT,R2
        JMP     @kvsend

ksus:   CLR.W   R1                       ; R3 = the sustain target (Q15) = LVLMAX * lvl(SUSTAIN)
        MOV.B   @K_SUS,R1
        MOV.W   #kt_lvl,R2
        JSR     @lk32
        SHAR.W  R4
        MOV.W   R4,R3
kvel:   MOV.W   R6,R0                    ; R3 = level (Q15) -> R3 = level * velocity of voice R6 / 128
        ADD.W   #K_VVEL,R0
        CLR.W   R4
        MOV.B   @R0,R4
        SWAP.B  R4
        JSR     @mulq
        MOV.W   R4,R3
        RTS

; ---- notes ----
; kvoices: R6 = voice -> R1 = chord size, R3 = semitone offset of voice R6 (signed byte)
kvoices:
        CLR.W   R2
        MOV.B   @K_CHORD,R2
        MOV.W   R2,R3                    ; * 9
        SHLL.W  R2
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

; kpanel: R6 = voice, R3 = its chord offset -> a panel voice playing NOTE + offset at full velocity
kpanel: MOV.W   R6,R0
        ADD.W   #K_VK,R0
        MOV.B   #0xFF,@R0
        MOV.B   #127,@(K_VVEL-K_VK,R0)
        CLR.W   R1
        MOV.B   @K_NOTE,R1
        ADD.W   R3,R1
        MOV.B   R1,@(K_VN-K_VK,R0)
        RTS

; kdl: R6 = voice -> R4:R5 = DL for its note: SYNTH = concert pitch (kt_nsyn, the DSP's 48 kHz / 64 = 750 Hz
; wavetable), SAMPLE = note - ROOT (ROOT = the recording's own note), both clamped to the tables
kdl:    MOV.W   R6,R0
        ADD.W   #K_VN,R0
        CLR.W   R1
        MOV.B   @R0,R1
        TST.B   @K_MODE
        BNE     kdl3
        CMP.W   #127,R1
        BLE     kdl4
        MOV.W   #127,R1
kdl4:   MOV.W   #kt_nsyn,R2
        JMP     @lk32
kdl3:   CLR.W   R3
        MOV.B   @K_ROOT,R3
        SUB.W   R3,R1
        ADD.W   #64,R1
        BPL     kdl1
        CLR.W   R1
kdl1:   CMP.W   #127,R1
        BLE     kdl2
        MOV.W   #127,R1
kdl2:   MOV.W   #kt_note,R2
        JMP     @lk32

knote_on:                                ; R6 = voice
        JSR     @kdl
        MOV.W   #KA_DL,R2
        JSR     @kvsend
        CLR.W   R4
        CLR.W   R5
        MOV.W   #KA_PH,R2                ; restart at the sample start
        JSR     @kvsend
        MOV.W   #LVLMAX,R3
        JSR     @kvel
        MOV.W   #K_ATK,R0
        JSR     @kenv
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

knote_off:                               ; R6 = voice (no key owns it any more)
        MOV.W   R6,R0
        ADD.W   #K_VK,R0
        MOV.B   #0xFF,@R0
        MOV.W   R6,R0
        ADD.W   #K_VST,R0
        TST.B   @R0
        BEQ     kno9
        MOV.B   #3,@R0
        MOV.W   #K_REL,R0
        CLR.W   R3
        JSR     @kenv
kno9:   RTS

kh_chord:                                ; a new chord retriggers PLAY (MIDI keys pick it up at their next note)
        TST.B   @K_PLAY
        BNE     kh_play
        RTS
kh_play:                                 ; (re)trigger the chord, or release everything
        CLR.W   R6
kpl1:   TST.B   @K_PLAY
        BEQ     kpl3
        JSR     @kvoices
        CMP.W   R1,R6
        BCC     kpl3
        JSR     @kpanel
        JSR     @knote_on
        BRA     kpl4
kpl3:   JSR     @knote_off
kpl4:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     kpl1
        RTS

kh_note:                                 ; NOTE / ROOT: retune the sounding voices, no retrigger
        CLR.W   R6                       ; (MIDI voices keep their key, ROOT still moves them)
knt1:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        MOV.B   @R0,R1
        BEQ     knt2
        CMP.B   #3,R1
        BEQ     knt2
        MOV.W   R6,R0
        ADD.W   #K_VK,R0
        CMP.B   #0xFF,@R0
        BNE     knt3
        JSR     @kvoices
        JSR     @kpanel
knt3:   JSR     @kdl
        MOV.W   #KA_DL,R2
        JSR     @kvsend
knt2:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     knt1
        RTS

kh_sus:                                  ; voices in sustain follow SUSTAIN at once
        CLR.W   R6
kss1:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        CMP.B   #2,@R0
        BNE     kss2
        JSR     @ksus
        MOV.W   #K_DEC,R0
        JSR     @kenv
kss2:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     kss1
        RTS

kh_rel:                                  ; releasing voices follow RELEASE at once
        CLR.W   R6
krl1:   MOV.W   R6,R0
        ADD.W   #K_VST,R0
        CMP.B   #3,@R0
        BNE     krl2
        MOV.W   #K_REL,R0
        CLR.W   R3
        JSR     @kenv
krl2:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     krl1
        RTS

; ---- MIDI IN: every channel, whatever Sony's channel / OMNI setting ----
; midi_rx: Sony's MIDI IN interrupt (0x1F16) calls here for every byte, in place of its MOV.B @FFDD,R1, and gets R1 back:
; the byte (page closed), or 0xF9 while the page is open (an undefined realtime byte Sony skips: no program changes or
; controllers reach Sony meanwhile). R0 is free, DP = 0 in, interrupts masked. Every byte is parsed, page open or not:
; note on / off, the sustain pedal, all notes off and CC 20-38 (the page's settings) go into the K_MQ queue (keys_midi
; plays it while the page is open), CC 119 opens (>= 64) / leaves the page (K_GO), SysEx F0 7D 52 .. F7 is the link
; (tools/keyslink.py): 10 = data into K_BUF, 11 = load K_BUF into the TMS57002, 12 = one coefficient, 13 = back to
; the ROM image, 14 = one setting.
midi_rx:
        MOV.B   @0xFFDD,R1
        LDC.B   #4,DP
        JSR     @mrx_byte
        TST.B   @K_OWN
        BEQ     mrx_r
        MOV.B   #0xF9,R1
mrx_r:  RTS
mrx_byte:
        BTST.B  #7,R1
        BEQ     mrx2
        CMP.B   #0xF8,R1                 ; realtime (clock ...): running status and SysEx stay
        BCC     mrx9
        CMP.B   #0xF7,R1
        BNE     mrx0
        CMP.B   #2,@K_SX                 ; F7 ending our SysEx: its command goes to keys_tick
        BNE     mrx01
        TST.B   @K_SXCMD
        BNE     mrx01                    ; (one at a time: dropped while one is pending)
        MOV.B   @K_SXB,R0
        CMP.B   #0x10,R0
        BEQ     mrx01                    ; data: nothing to run
        MOV.B   R0,@K_SXCMD
mrx01:  CLR.B   @K_SX
        BRA     mrx02
mrx0:   CLR.B   @K_SX                    ; any other status byte ends a SysEx
        CMP.B   #0xF0,R1
        BNE     mrx02
        MOV.B   #1,@K_SX                 ; F0: the manufacturer byte next
mrx02:  MOV.B   R1,@K_MST
        CMP.B   #0xF0,R1
        BCS     mrx1
        CLR.B   @K_MST                   ; SysEx / system common: no running status
mrx1:   MOV.B   #0xFF,@K_MD1
mrx9:   RTS
mrx2:   TST.B   @K_SX                    ; a data byte: inside a SysEx?
        BEQ     mrx20
        JMP     @mrx_sx
mrx20:  MOV.B   @K_MST,R0                ; 8n / 9n / Bn take two
        AND.B   #0xF0,R0
        CMP.B   #0xB0,R0
        BEQ     mrx3
        AND.B   #0xE0,R0
        CMP.B   #0x80,R0
        BNE     mrx9
mrx3:   CMP.B   #0xFF,@K_MD1
        BNE     mrx4
        MOV.B   R1,@K_MD1                ; the key / controller number
        RTS
mrx4:   MOV.W   R2,@-SP
        MOV.W   R3,@-SP
        MOV.B   @K_MD1,R2                ; R2 = event key, R3 = value
        MOV.B   #0xFF,@K_MD1
        MOV.B   R1,R3
        MOV.B   @K_MST,R0
        AND.B   #0xF0,R0
        CMP.B   #0x90,R0
        BEQ     mrx7
        CMP.B   #0x80,R0
        BNE     mrx5
        CLR.B   R3                       ; note off = velocity 0
        BRA     mrx7
mrx5:   CMP.B   #64,R2                   ; controllers: sustain pedal, all notes off (120 / 123), settings, the page
        BNE     mrx6
        MOV.B   #0x80,R2
        BRA     mrx7
mrx6:   CMP.B   #120,R2
        BEQ     mrx61
        CMP.B   #123,R2
        BNE     mrx62
mrx61:  MOV.B   #0x81,R2
        BRA     mrx7
mrx62:  CMP.B   #119,R2                  ; CC 119: open (>= 64) / leave the page, from keys_tick
        BNE     mrx63
        MOV.B   #2,R0
        CMP.B   #64,R3
        BCS     mrx64
        MOV.B   #1,R0
mrx64:  MOV.B   R0,@K_GO
        BRA     mrx8
mrx63:  SUB.B   #20,R2                   ; CC 20 .. 20 + KS_N - 1: setting R2 - 20 -> event 0xA0 + n
        CMP.B   #KS_N,R2
        BCC     mrx8
        ADD.B   #0xA0,R2
mrx7:   CLR.W   R0
        MOV.B   @K_MQW,R0
        SHLL.W  R0
        MOV.B   R2,@(K_MQ,R0)
        MOV.B   R3,@(K_MQ+1,R0)
        SHLR.W  R0
        ADD.B   #1,R0
        AND.B   #15,R0
        CMP.B   @K_MQR,R0
        BEQ     mrx8                     ; full: dropped
        MOV.B   R0,@K_MQW
mrx8:   MOV.W   @SP+,R3
        MOV.W   @SP+,R2
        RTS

; mrx_sx: a SysEx data byte (R1). K_SX: 1 manufacturer next, 4 'R' (0x52) next, 2 ours, 3 not ours.
; Ours: byte 0 = command, then 10: offset (2 x 7 bits) + nibble pairs (high first) into K_BUF; others: K_SXB[1..11].
mrx_sx: MOV.B   @K_SX,R0
        CMP.B   #1,R0
        BNE     msx1
        MOV.B   #3,@K_SX
        CMP.B   #0x7D,R1                 ; non-commercial ID
        BNE     msx9
        MOV.B   #4,@K_SX
        RTS
msx1:   CMP.B   #4,R0
        BNE     msx2
        MOV.B   #3,@K_SX
        CMP.B   #0x52,R1
        BNE     msx9
        MOV.B   #2,@K_SX
        CLR.B   @K_SXN
        RTS
msx2:   CMP.B   #2,R0
        BNE     msx9
        MOV.W   R2,@-SP
        MOV.W   R3,@-SP
        CLR.W   R2
        MOV.B   @K_SXN,R2
        BNE     msx3
        MOV.B   R1,@K_SXB                ; the command
        MOV.B   #0xFF,@K_SXHI
        BRA     msx7
msx3:   CMP.B   #0x10,@K_SXB
        BNE     msx5
        CMP.W   #3,R2
        BCS     msx5
        MOV.B   @K_SXHI,R0               ; data: a nibble pair is one byte
        CMP.B   #0xFF,R0
        BNE     msx4
        MOV.B   R1,@K_SXHI
        BRA     msx8
msx4:   SHLL.B  R0
        SHLL.B  R0
        SHLL.B  R0
        SHLL.B  R0
        AND.B   #0x0F,R1
        OR.B    R1,R0
        MOV.B   #0xFF,@K_SXHI
        MOV.W   @K_SXOFF,R2
        CMP.W   #KEYS_PROG_LEN+KEYS_CMEM_LEN,R2
        BCC     msx8
        MOV.B   R0,@(K_BUF,R2)
        ADD.W   #1,R2
        MOV.W   R2,@K_SXOFF
        BRA     msx8
msx5:   CMP.W   #12,R2                   ; staging
        BCC     msx7
        MOV.B   R1,@(K_SXB,R2)
        CMP.W   #2,R2
        BNE     msx7
        CMP.B   #0x10,@K_SXB             ; 10: the offset is in -> K_SXOFF = hi * 128 + lo
        BNE     msx7
        CLR.W   R3
        MOV.B   @K_SXB+1,R3
        SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        CLR.W   R2
        MOV.B   R1,R2
        ADD.W   R2,R3
        MOV.W   R3,@K_SXOFF
msx7:   MOV.B   @K_SXN,R0                ; count (stops at 255)
        CMP.B   #0xFF,R0
        BEQ     msx8
        ADD.B   #1,R0
        MOV.B   R0,@K_SXN
msx8:   MOV.W   @SP+,R3
        MOV.W   @SP+,R2
msx9:   RTS

; keys_midi (from keys_tick): play the queued events
keys_midi:
kmi1:   MOV.B   @K_MQR,R0
        CMP.B   @K_MQW,R0
        BEQ     kmi9
        CLR.W   R2
        MOV.B   R0,R2
        SHLL.W  R2
        MOV.B   @(K_MQ,R2),R1
        MOV.B   R1,@K_MKEY
        MOV.B   @(K_MQ+1,R2),R3
        MOV.B   R3,@K_MVEL
        ADD.B   #1,R0
        AND.B   #15,R0
        MOV.B   R0,@K_MQR
        CMP.B   #0xA0,R1
        BCC     kmi5
        CMP.B   #0x80,R1
        BEQ     kmi3
        BCC     kmi4
        TST.B   R3
        BEQ     kmi2
        JSR     @kmidi_on
        BRA     kmi1
kmi2:   JSR     @kmidi_off
        BRA     kmi1
kmi3:   JSR     @kmidi_ped
        BRA     kmi1
kmi4:   JSR     @kmidi_all
        BRA     kmi1
kmi5:   SUB.B   #0xA0,R1                 ; CC 20 + n: setting n = value (clamped); redraw_loop sends it
        JSR     @kset
        BRA     kmi1

; kset: R1 = setting (< KS_N), R3 = value -> K_SET (clamped to kcc_max), K_REDRAW
kset:   CLR.W   R2
        MOV.B   R1,R2
        LDC.B   #0,DP
        MOV.B   @(kcc_max,R2),R0
        LDC.B   #4,DP
        CMP.B   R0,R3
        BLS     kst1
        MOV.B   R0,R3
kst1:   MOV.B   R3,@(K_SET,R2)
        MOV.B   #1,@K_REDRAW
        RTS

; ---- CC 119 (K_GO): open / leave this page like a SUB row / EXIT would ----
keys_go:                                 ; R0 = 1 open, 2 leave
        MOV.W   @E_PAGE,R2
        CMP.B   #1,R0
        BNE     kgo5
        CMP.W   #pg_keys,R2
        BEQ     kgo9
        CLR.W   R1
        MOV.B   @E_DEPTH,R1
        CMP.B   #4,R1
        BCC     kgo9
        MOV.W   R1,R3                    ; E_STACK + depth * 4 <- page, sel, top
        SHLL.W  R3
        SHLL.W  R3
        ADD.W   #E_STACK,R3
        MOV.W   R2,@R3
        MOV.B   @E_SEL,R0
        MOV.B   R0,@(2,R3)
        MOV.B   @E_TOP,R0
        MOV.B   R0,@(3,R3)
        ADD.B   #1,R1
        MOV.B   R1,@E_DEPTH
        MOV.W   #pg_keys,R0
        MOV.W   R0,@E_PAGE
        CLR.B   @E_SEL
        CLR.B   @E_TOP
        CLR.B   @E_EDIT
        MOV.B   #1,@K_REDRAW
        RTS
kgo5:   CMP.W   #pg_keys,R2
        BNE     kgo9
        CLR.W   R1
        MOV.B   @E_DEPTH,R1
        BEQ     kgo9
        SUB.B   #1,R1
        MOV.B   R1,@E_DEPTH
        MOV.W   R1,R3
        SHLL.W  R3
        SHLL.W  R3
        ADD.W   #E_STACK,R3
        MOV.W   @R3,R0
        MOV.W   R0,@E_PAGE
        MOV.B   @(2,R3),R0
        MOV.B   R0,@E_SEL
        MOV.B   @(3,R3),R0
        MOV.B   R0,@E_TOP
        CLR.B   @E_EDIT
        MOV.B   #1,@K_REDRAW
kgo9:   RTS

; ---- the link's SysEx commands (page open) ----
keys_sxcmd:
        MOV.B   @K_SXCMD,R0
        BNE     ksx0
        RTS
ksx0:   CMP.B   #0x12,R0
        BNE     ksx2
        MOV.W   #K_SXB+3,R2              ; 12 a1 a0 + 8 nibbles (high first): coefficient a1 * 128 + a0
        JSR     @ksx_w
        MOV.W   R3,R4
        JSR     @ksx_w
        MOV.W   R3,R5
        MOV.B   @K_SXB+2,R1
        TST.B   @K_SXB+1
        BEQ     ksx1
        OR.B    #0x80,R1
ksx1:   JSR     @kcw
        BRA     ksx8
ksx2:   CMP.B   #0x11,R0                 ; 11: load K_BUF
        BNE     ksx3
        MOV.B   #1,@K_LSRC
        JSR     @keys_load
        BRA     ksx8
ksx3:   CMP.B   #0x13,R0                 ; 13: back to the ROM image
        BNE     ksx4
        CLR.B   @K_LSRC
        JSR     @keys_load
        BRA     ksx8
ksx4:   CMP.B   #0x14,R0                 ; 14 n v: setting n = v
        BNE     ksx8
        MOV.B   @K_SXB+1,R1
        CMP.B   #KS_N,R1
        BCC     ksx8
        CLR.W   R3
        MOV.B   @K_SXB+2,R3
        JSR     @kset
ksx8:   CLR.B   @K_SXCMD
        RTS
ksx_w:  CLR.W   R3                       ; @R2+: 4 nibbles (high first) -> R3
        JSR     @ksx_n
        JSR     @ksx_n
        JSR     @ksx_n
ksx_n:  SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        SHLL.W  R3
        MOV.B   @R2+,R0
        AND.W   #0x000F,R0
        OR.W    R0,R3
        RTS

; keys_reset: Sony's reset routine calls here at every power-on (0x0213, in place of its LDC.B #4,DP after the first
; init calls), long before Sony's boot DSP load. K_OWN is battery-backed: left at 1 from the last session (powered off
; on this page) it gated that load, and the DSP ran random program memory until ros_entry (loud noise at boot).
keys_reset:
        LDC.B   #4,DP
        CLR.B   @K_OWN
        RTS

; power-on (ros_entry): page / link requests from before don't count
keys_boot:
        LDC.B   #4,DP
        CLR.B   @K_GO
        CLR.B   @K_REDRAW
        CLR.B   @K_SX
        CLR.B   @K_SXCMD
        CLR.B   @K_LSRC
        RTS
kmi9:   RTS

kmidi_on:                                ; K_MKEY / K_MVEL: a voice per chord tone (CHORD 1 NOTE = 8-voice poly)
        JSR     @kmidi_off               ; the same key again: its old voices let go first
        CLR.B   @K_MJ
kmo1:   CLR.W   R6
        MOV.B   @K_MJ,R6
        JSR     @kvoices                 ; R1 = chord size, R3 = this tone's offset
        CMP.W   R1,R6
        BCC     kmo9
        CLR.W   R1
        MOV.B   @K_MKEY,R1
        ADD.W   R3,R1
        MOV.B   R1,@K_MN
        JSR     @kalloc                  ; R6 = the voice
        MOV.W   R6,R0
        ADD.W   #K_VK,R0
        MOV.B   @K_MKEY,R1
        MOV.B   R1,@R0
        MOV.B   @K_MN,R1
        MOV.B   R1,@(K_VN-K_VK,R0)
        MOV.B   @K_MVEL,R1
        MOV.B   R1,@(K_VVEL-K_VK,R0)
        JSR     @knote_on
        MOV.B   @K_MJ,R0
        ADD.B   #1,R0
        MOV.B   R0,@K_MJ
        BRA     kmo1
kmo9:   RTS

kmidi_off:                               ; K_MKEY lets go (pedal down: held, 0xFD, until the pedal comes up)
        CLR.W   R6
kmf1:   MOV.W   R6,R0
        ADD.W   #K_VK,R0
        MOV.B   @K_MKEY,R1
        CMP.B   @R0,R1
        BNE     kmf3
        TST.B   @K_PED
        BEQ     kmf2
        MOV.B   #0xFD,@R0
        BRA     kmf3
kmf2:   JSR     @knote_off
kmf3:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     kmf1
        RTS

kmidi_ped:                               ; K_MVEL = CC 64 value: >= 64 down; up lets the held voices go
        MOV.B   @K_MVEL,R0
        CMP.B   #64,R0
        BCS     kpd1
        MOV.B   #1,@K_PED
        RTS
kpd1:   CLR.B   @K_PED
        MOV.B   #0xFD,@K_MKEY
        JMP     @kmidi_off

kmidi_all:                               ; all notes off: every MIDI voice lets go (the panel's PLAY chord stays)
        CLR.B   @K_PED
        CLR.W   R6
kma1:   MOV.W   R6,R0
        ADD.W   #K_VK,R0
        CMP.B   #0xFF,@R0
        BEQ     kma2
        JSR     @knote_off
kma2:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     kma1
        RTS

; kalloc: -> R6 = the voice to take: a free one, else the oldest releasing one, else the oldest
kalloc: CLR.W   R6
        CLR.W   R2                       ; best score so far
        CLR.W   R1
kal1:   MOV.W   R1,R0
        ADD.W   #K_VAGE,R0
        CLR.W   R3
        MOV.B   @K_SEQ,R3
        SUB.B   @R0,R3                   ; age, 0..255
        ADD.W   #1,R3
        MOV.W   R1,R0
        ADD.W   #K_VST,R0
        TST.B   @R0
        BNE     kal2
        ADD.W   #0x200,R3
kal2:   CMP.B   #3,@R0
        BNE     kal3
        ADD.W   #0x100,R3
kal3:   CMP.W   R2,R3
        BLS     kal4
        MOV.W   R3,R2
        MOV.W   R1,R6
kal4:   ADD.W   #1,R1
        CMP.W   #KNV,R1
        BNE     kal1
        MOV.B   @K_SEQ,R1
        ADD.B   #1,R1
        MOV.B   R1,@K_SEQ
        MOV.W   R6,R0
        ADD.W   #K_VAGE,R0
        MOV.B   R1,@R0
        RTS

; ---- recording (SAMPLE) ----
keys_record:                             ; K_ACT: 1.49 s from the chosen input (SYNTH: nothing to do)
        LDC.B   #4,DP
        TST.B   @K_OWN
        BEQ     krc9
        TST.B   @K_MODE
        BEQ     krc9
        MOV.W   #0x4000,R4               ; that input at 0.5 into the record mix
        CLR.W   R5
        MOV.W   #KA_RGIN,R2
        TST.B   @K_SRC
        BEQ     krc1
        ADD.W   #1,R2
krc1:   LDC.B   #0,DP
        MOV.B   @R2,R1
        LDC.B   #4,DP
        JSR     @kcw
        CLR.W   R4                       ; AGE = 0: the sample starts now
        MOV.W   #KA_AMASK,R1
        JSR     @kcw
        MOV.W   #0xFFFF,R4
        MOV.W   #0xFFFF,R5
        MOV.W   #KA_AMASK,R1
        JSR     @kcw
        MOV.W   #0x8000,R4               ; MREC = -1: the slot gets the input
        CLR.W   R5
        MOV.W   #KA_MREC,R1
        JSR     @kcw
        MOV.W   #REC_TICKS,R0
        MOV.W   R0,@K_RECN
krc9:   RTS

; ---- from k_idle (every main-loop pass): envelope decay switch, end of recording ----
keys_tick:                               ; -> R0 = 1: k_idle goes through redraw_loop
        LDC.B   #4,DP
        MOV.B   @K_GO,R0
        BEQ     ktw1
        CLR.B   @K_GO
        JSR     @keys_go
ktw1:   JSR     @ktick
        LDC.B   #4,DP
        CLR.W   R0
        MOV.B   @K_REDRAW,R0
        CLR.B   @K_REDRAW
        RTS
ktick:  TST.B   @K_OWN
        BEQ     ktk9
        JSR     @keys_midi
        JSR     @keys_sxcmd
        LDC.B   #4,DP
        MOV.B   @L_TICK,R0
        MOV.B   R0,R1
        SUB.B   @K_LASTT,R0              ; R0 = ticks since the last pass
        BEQ     ktk9
        MOV.B   R1,@K_LASTT
        CLR.W   R3
        MOV.B   R0,R3
        MOV.W   R3,@K_TMP+2
        MOV.W   @K_RECN,R1               ; recording
        BEQ     ktk2
        SUB.W   R3,R1
        BGT     ktk1
        CLR.W   R4                       ; done: inputs out, MREC = 0 (the RAM stays)
        CLR.W   R5
        MOV.W   R5,@K_RECN
        MOV.W   #KA_RGIN,R2
        MOV.W   #2,R3
        JSR     @ksend_n
        MOV.W   #KA_MREC,R1
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
        SUB.W   @K_TMP+2,R1
        MOV.W   R1,@R2
        BGT     ktk5
        MOV.B   #2,@R0                   ; attack over: decay to SUSTAIN
        MOV.W   @K_TMP+2,R0
        MOV.W   R0,@-SP
        JSR     @ksus
        MOV.W   #K_DEC,R0
        JSR     @kenv
        MOV.W   @SP+,R0
        MOV.W   R0,@K_TMP+2
ktk5:   ADD.W   #1,R6
        CMP.W   #KNV,R6
        BNE     ktk3
ktk9:   RTS

; ---- program in / out ----
keys_load:
        MOV.B   #1,@K_OWN                ; gate Sony first
        MOV.B   #0,@K_PLAY               ; start silent, SYNTH with the sine
        MOV.B   #0,@K_MODE
        STC.W   SR,R0
        MOV.W   R0,@-SP
        ORC.W   #0x0700,SR
        MOV.W   R6,@-SP
        LDC.B   #0,EP                    ; PLOAD: ST0, ST1, 256 words (PLOAD stays on: the DSP waits)
        MOV.W   #keys_prog,R5            ;   from the ROM image or the link's K_BUF
        TST.B   @K_LSRC
        BEQ     kld0
        LDC.B   #4,EP
        MOV.W   #K_BUF,R5
kld0:   LDC.B   #0,DP
        MOV.W   #KEYS_PROG_LEN-1,R6
        PJSR    @KST_PL
        LDC.B   #4,DP
        LDC.B   #0,EP                    ; + CLOAD: all 256 coefficients, then both released
        MOV.W   #keys_cmem,R5
        TST.B   @K_LSRC
        BEQ     kld0b
        LDC.B   #4,EP
        MOV.W   #K_BUF+KEYS_PROG_LEN,R5
kld0b:  LDC.B   #0,DP
        MOV.W   #KEYS_CMEM_LEN-1,R6
        PJSR    @KST_CL
kld_done:
        LDC.B   #4,DP
        MOV.W   @SP+,R6
        MOV.W   @SP+,R0
        LDC.W   R0,SR
        CLR.W   R1                       ; every setting goes out once (ROM image); none for the link's image
kld1:   MOV.B   #0xFF,R2                 ;   (its coefficients need not be ours: the link sets them)
        TST.B   @K_LSRC
        BEQ     kld1a
        MOV.B   @(K_SET,R1),R2
kld1a:  MOV.B   R2,@(K_SENT,R1)
        ADD.W   #1,R1
        CMP.W   #KS_N,R1
        BNE     kld1
        CLR.W   R0
        MOV.W   R0,@K_RECN
        MOV.W   #K_VST,R1
kld2:   MOV.W   R0,@R1
        ADD.W   #2,R1
        CMP.W   #K_VST+8,R1
        BNE     kld2
        MOV.W   #K_VK,R1                 ; MIDI: no keys, pedal up, parser + queue empty
kld3:   MOV.B   #0xFF,@R1
        MOV.B   #127,@(K_VVEL-K_VK,R1)
        ADD.W   #1,R1
        CMP.W   #K_VK+8,R1
        BNE     kld3
        CLR.B   @K_PED
        CLR.B   @K_MST
        MOV.B   #0xFF,@K_MD1
        MOV.B   @K_MQW,R0
        MOV.B   R0,@K_MQR
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
gate_mr:                                 ; 0x13E43: meter read (waits for TMS57002 host data our program never gives)
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
        .db     20, 0
        .dw     l_kplay
        .db     K_LIST, 1
        .dw     K_PLAY, n_offon
        .dw     l_knote
        .db     K_DYN, 0
        .dw     K_NOTE, dyn_note
        .dw     l_kchord
        .db     K_LIST, 7
        .dw     K_CHORD, n_kchord
        .dw     l_kmode
        .db     K_LIST, 1
        .dw     K_MODE, n_kmode
        .dw     l_ksine
        .db     K_NUM, 99
        .dw     K_SINE, 0
        .dw     l_ksaw
        .db     K_NUM, 99
        .dw     K_SAW, 0
        .dw     l_kpul
        .db     K_NUM, 99
        .dw     K_PUL, 0
        .dw     l_kpw
        .db     K_NUM, 99
        .dw     K_PW, 0
        .dw     l_ksrc
        .db     K_LIST, 1
        .dw     K_SRC, n_ksrc
        .dw     l_krec
        .db     K_ACT, 0
        .dw     keys_record, 0
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
        .dw     l_kvol
        .db     K_NUM, 99
        .dw     K_VOL, 0
        .dw     l_kthru
        .db     K_NUM, 99
        .dw     K_THRU, 0
        .dw     l_kroot
        .db     K_DYN, 0
        .dw     K_ROOT, dyn_note

n_kchord: .dw   c_one, c_oct, c_fifth, c_maj, c_min, c_maj7, c_min7, c_stack
n_kmode:  .dw   s_synth, s_sample
n_ksrc:   .dw   s_ina, s_inb
kcc_max:  .db   1, 127, 7, 1, 99, 99, 99, 99, 1, 99, 99, 99, 99, 99, 99, 99, 99, 99, 127   ; CC 20-38 limits
          .align 2

t_keys:   .asciz "DDS-DSP SAMPLER"
l_keys:   .asciz "DDS-DSP SAMPLER"
s_keys:   .asciz "8V MIDI"
l_kplay:  .asciz "PLAY"
l_knote:  .asciz "NOTE"
l_kchord: .asciz "CHORD"
l_kmode:  .asciz "MODE"
l_ksine:  .asciz "SINE"
l_ksaw:   .asciz "SAW"
l_kpul:   .asciz "PULSE"
l_kpw:    .asciz "WIDTH"
l_ksrc:   .asciz "SOURCE"
l_krec:   .asciz "RECORD"
l_katk:   .asciz "ATTACK"
l_kdec:   .asciz "DECAY"
l_ksus:   .asciz "SUSTAIN"
l_krel:   .asciz "RELEASE"
l_kcut:   .asciz "CUTOFF"
l_kres:   .asciz "RESONANCE"
l_kspr:   .asciz "SPREAD"
l_kvol:   .asciz "VOLUME"
l_kthru:  .asciz "THRU"
l_kroot:  .asciz "ROOT"
c_one:    .asciz "1 NOTE"
c_oct:    .asciz "OCTAVE"
c_fifth:  .asciz "FIFTH"
c_maj:    .asciz "MAJOR"
c_min:    .asciz "MINOR"
c_maj7:   .asciz "MAJ 7"
c_min7:   .asciz "MIN 7"
c_stack:  .asciz "8 STACK"
s_synth:  .asciz "SYNTH"
s_sample: .asciz "SAMPLE"
s_ina:    .asciz "INPUT A"
s_inb:    .asciz "INPUT B"
kt_nn:    .ascii "C C#D D#E F F#G G#A A#B "
kt_chord: .db   1, 0, 0, 0, 0, 0, 0, 0, 0      ; voices, then 8 semitone offsets
          .db   2, 0, 12, 0, 0, 0, 0, 0, 0
          .db   2, 0, 7, 0, 0, 0, 0, 0, 0
          .db   3, 0, 4, 7, 0, 0, 0, 0, 0
          .db   3, 0, 3, 7, 0, 0, 0, 0, 0
          .db   4, 0, 4, 7, 11, 0, 0, 0, 0
          .db   4, 0, 3, 7, 10, 0, 0, 0, 0
          .db   8, 0, 7, 12, 16, 19, 24, 28, 31
