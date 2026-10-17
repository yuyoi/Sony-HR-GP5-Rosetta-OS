# Find the HR-MP5 equivalents of the HR-GP5 addresses Rosetta uses: search each GP5 routine's opening bytes
# (several window sizes) in the MP5 ROM and report unique hits.
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
GP5 = open(os.path.join(HERE, "..", "dumps", "hrgp5_eprom_read1.bin"), "rb").read()
MP5 = open(os.path.join(HERE, "..", "dumps", "mp5_rom1_11j", "rom1_11j.bin"), "rb").read()

ANCHORS = [("ui_dispatch", 0x0672), ("key_bypass", 0x0824), ("key_meter_tune", 0x0960), ("scr_save", 0x3198),
           ("scr_system_select", 0x481F), ("draw_begin", 0x04C5), ("draw_end", 0x04CD), ("ui_poll_a", 0x1B03),
           ("ui_poll_b", 0x0790), ("ui_after_draw", 0x43AD), ("boot_screen", 0x2558), ("boot_call", 0x063E),
           ("tick_0xC0", 0x04FD), ("prog_addr", 0x1870), ("prog_addr_dp", 0x18A9), ("lcd_init", 0x0F58),
           ("test_mode", 0x0256), ("pageset_open", 0x36AC), ("coef_write", 0x156B3), ("meter_read", 0x14D9A),
           ("meter_proc", 0x14C04), ("dsp_periodic", 0x14397), ("FONT", 0xA7C2), ("adcsr_init", 0x0208)]

def hits(pat):
    out, i = [], MP5.find(pat)
    while i >= 0:
        out.append(i); i = MP5.find(pat, i + 1)
    return out

for name, a in ANCHORS:
    res = None
    for n in (16, 12, 10, 8, 6):
        for off in (0, 2, 4):
            h = hits(GP5[a + off:a + off + n])
            if len(h) == 1:
                res = (h[0] - off, n, off); break
        if res: break
    if res: print("%-18s GP5 %05X -> MP5 %05X  (%d bytes @+%d)" % (name, a, res[0], res[1], res[2]))
    else: print("%-18s GP5 %05X -> ?" % (name, a))

# free space: runs of FF >= 256 bytes
runs, i = [], 0
while i < len(MP5):
    if MP5[i] == 0xFF:
        j = i
        while j < len(MP5) and MP5[j] == 0xFF: j += 1
        if j - i >= 256: runs.append((i, j - i))
        i = j
    else: i += 1
print("MP5 FF runs >= 256 B:", ", ".join("%05X+%d" % r for r in runs))
