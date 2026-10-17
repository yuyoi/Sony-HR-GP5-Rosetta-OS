; HR-MP5 Ver1.11 addresses used by Rosetta (found by tools/port_anchors.py + by hand). Assembled before rosetta.asm.
; RAM layout (edit buffer 0x458A0, DSP program 0x41280, flags 0x462E0, meters FD86-FD89) is the same as the GP5.
        .equ ui_dispatch, 0x0672
        .equ key_bypass, 0x083A
        .equ key_meter_tune, 0x0976
        .equ scr_save, 0x31B6
        .equ scr_system_select, 0x483D
        .equ draw_begin, 0x04C5
        .equ draw_end, 0x04CD
        .equ ui_poll_a, 0x1B21
        .equ ui_poll_b, 0x0790
        .equ ui_after_draw, 0x43CB
        .equ FONT, 0xADA6                ; small 7-row font (A-Z glyphs at 0xAF6D)
        .equ EDTAB, 0x860E               ; 54 ids: EQ x2, EFCT1 x29, EQ x2, EFCT2 x20, LEVEL
        .equ SONY_TICK, 0x04FD
        .equ CWRITE_P1, 0x1FFB2          ; in the FF tail of page 1
        .equ NBLK, 4                     ; EQ1, EFCT1, EQ2, EFCT2 (Sony's block routine at 0xAAED)
        .equ LFO_DEF_BLOCK, 3            ; EFCT2
        .equ HAS_MSRC, 0                 ; c(05) not verified in the MP5 DSP program: no SOURCE row, no writes
        .equ INPUT_ROWS, 4
        .equ HAS_KEYS, 1                 ; DDS-DSP SAMPLER (src/keys.asm): Sony TMS57002 routine addresses below
        .equ SONY_CW_CONT, 0x1475A       ; 0x14755 coefficient write (R1, R4:R5), after its MOV.B #4,@FD10
        .equ SONY_CW_SKIP, 0x14791       ;   its RTS
        .equ SONY_CW2_CONT, 0x146E1      ; 0x146DA coefficient write (FE05 / FE06 / FE08)
        .equ SONY_CW2_SKIP, 0x14722      ;   MOV.W @SP+,R0 ; RTS
        .equ SONY_PL_CONT, 0x14690       ; 0x1468B program load (PLOAD), EP:R5 data, FP = count - 1
        .equ SONY_CL_CONT, 0x1467D       ; 0x14678 coefficient-memory load (PLOAD + CLOAD)
        .equ SONY_LD_SKIP, 0x146D0       ;   their RTS
        .equ SONY_MR_CONT, 0x13E48       ; 0x13E43 meter read (waits for TMS57002 host data)
        .equ SONY_MR_SKIP, 0x13E37       ;   Sony's own skip: CLR R4, CLR R5, RTS
        .equ KST_CW, 0x1FFB8             ; page-1 stubs past the gates (tools/rosetta_build.py writes them)
        .equ KST_PL, 0x1FFC2
        .equ KST_CL, 0x1FFCC
