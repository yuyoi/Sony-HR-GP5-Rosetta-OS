; Sony HR-GP5 Rosetta OS v0.1 - page-0 code, lives in the freed factory-preset area (0xB3C8-0xF7D7).
; Assembled by tools/h85asm.py from tools/rosetta_build.py, which also defines the stock symbols below.
;
; v0.1: the ROSETTA screen (SYSTEM -> B "ROSET"): title, big ROSETTA, credits.
;       It runs on Sony's own dispatcher (ui_dispatch 0x0672) with its own 13-slot key table.

        .equ ui_dispatch, 0x0672
        .equ key_bypass, 0x0824
        .equ key_meter_tune, 0x0960
        .equ scr_clear, 0x1021
        .equ big_str, 0x10B1
        .equ txt_str, 0x1ABD
        .equ scr_save, 0x3198
        .equ scr_system_select, 0x481F
        .equ scr_edit, 0x0AD24
        .equ draw_begin, 0x04C5          ; FFFD.0 off (stock draw routines bracket with these)
        .equ draw_end, 0x04CD
        .equ ui_poll_a, 0x1B03           ; every stock screen loop calls these two
        .equ ui_poll_b, 0x0790
        .equ ui_after_draw, 0x43AD
        .equ TXT, 0x05F0                 ; text layer in the RAM shadow (38 x 5)
        .equ ROW, 38

        .org ROSETTA_BASE

; ---------------- ROSETTA screen ----------------
ros_entry:
        LDC.B   #4,DP
        BSET.B  #0,@0x62E0               ; "not in PLAY", like the SYSTEM screens
        JSR     @ros_draw
ros_loop:
        JSR     @ui_poll_a
        JSR     @ui_poll_b
        LDC.B   #0,DP
        MOV.B   @0xFD9C,R1               ; ui_last_fkey
        MOV.W   #ros_table,R0
        JMP     @ui_dispatch

        .align 2
ros_table:                               ; k0..k11, dial (word table: must be even)
        .dw     ros_meter, ros_loop, ros_loop, ros_loop, ros_loop, ros_loop, ros_loop
        .dw     scr_edit, scr_system_select, scr_system_select, scr_save, ros_bypass, ros_loop

ros_meter:
        JSR     @key_meter_tune
        JSR     @ros_draw
        JMP     @ros_loop
ros_bypass:
        JSR     @key_bypass
        JSR     @ros_draw
        JMP     @ros_loop

ros_draw:
        JSR     @draw_begin
        JSR     @scr_clear
        LDC.B   #4,DP
        LDC.B   #0,EP
        MOV.W   #TXT,R0                  ; title, text row 0
        MOV.W   #s_title,R4
        JSR     @txt_str
        MOV.W   #s_big,R1                ; big 18-row text, gfx row 12, centred by the build
        MOV.W   #BIG_COL,R2
        JSR     @big_str
        LDC.B   #4,DP
        LDC.B   #0,EP
        MOV.W   #TXT+4*ROW+2,R0          ; credit line, text row 4
        MOV.W   #s_credit,R4
        JSR     @txt_str
        JSR     @ui_after_draw
        JSR     @draw_end
        RTS

s_title: .asciz "ROSETTA OS v0.1"          ; top-right corner is the firmware's status area
s_big:   .asciz "ROSETTA"
s_credit: .asciz "Hex girl inside   JSW + Claude"
