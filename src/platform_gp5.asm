; HR-GP5 Ver1.03 (stock EPROM GP5-E1) addresses used by Rosetta. Assembled before rosetta.asm.
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
        .equ EDTAB, 0x8044               ; EDIT page-set table: 8 B per effect id (descriptor.w + 6-char name)
        .equ SONY_TICK, 0x04FD           ; Sony's 5 ms tick handler (vector 0xC0)
        .equ CWRITE_P1, 0x1F598          ; page-1 stub: JSR @coefficient routine ; PRTS
        .equ NBLK, 6                     ; effect blocks A-F
        .equ LFO_DEF_BLOCK, 5            ; F (HALL)
        .equ HAS_MSRC, 1                 ; DSP meter SOURCE (c(05) = meter pointer, verified in the GP5 DSP program)
        .equ INPUT_ROWS, 5
        .equ HAS_KEYS, 0                 ; DDS-DSP SAMPLER is MP5-only so far (the GP5 has two copies of the TMS routines)
