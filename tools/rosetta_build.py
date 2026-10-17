# Build Sony HR-GP5 Rosetta OS from your stock Ver1.03E dump:
#   python -I tools/rosetta_build.py            -> build/hrgp5_rosetta_os.bin (+ _SST39SF040_x4.bin, listings, screenshots)
# Steps: strip factory presets (programs 0-99 -> one INIT program), TEST mode off, ROSETTA code into the freed
# page-0 space, SYSTEM:Select B = ROSET, Hex girl boot in page 1, then boot it in the emulator and screenshot.
import sys, os, hashlib, struct
sys.path.insert(0, os.path.dirname(__file__))
from h85asm import Asm
import anim_build, gp5emu

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VERSION = "0.3.4"
PRESETS, PRESET_LEN = 0xB318, 0xB0
INIT_FROM = 8                        # "Transparnt" becomes the single INIT program
FREE0, FREE0_END = PRESETS + PRESET_LEN, 0xF7D8
ROSETTA_BASE = 0xB3D0
CWRITE_P1 = 0x1F598                  # page-1 stub in the FF gap before the boot player (0x1F596-0x1F59F)


def patch(rom, addr, old, new):
    old = bytes.fromhex(old) if isinstance(old, str) else old
    new = bytes.fromhex(new) if isinstance(new, str) else new
    assert len(old) == len(new), hex(addr)
    assert rom[addr:addr + len(old)] == old, "patch %05X: expected %s, found %s" % (addr, old.hex(" "), rom[addr:addr + len(old)].hex(" "))
    rom[addr:addr + len(new)] = new


def big_width(rom, text):
    w = 0
    for ch in text.encode():
        p = 0x10000 | ((rom[0x112F4 + (ch & 0x7F) * 2] << 8) | rom[0x112F5 + (ch & 0x7F) * 2])
        w += rom[p]
    return w


BLOCKS = [(0x3C, 0x00), (0x44, 0x03), (0x4C, 0x0A), (0x5C, 0x0D), (0x78, 0x23), (0x94, 0x2B)]   # program offset, EDIT id base


def edit_table_len(rom):
    n = 0
    while True:
        e = 0x8044 + n * 8
        p = (rom[e] << 8) | rom[e + 1]
        name = rom[e + 2:e + 8]
        if not (0x7000 <= p < 0xB000 and name[-1] == 0 and all(32 <= c < 127 for c in name[:name.index(0)])): return n
        n += 1


def gen_tables(rom):
    """LFO rate table (0.05-15 Hz at a 100 Hz update), sine table, block table (offset, id base, type count)"""
    import math
    n = edit_table_len(rom)
    bases = [b for o, b in BLOCKS] + [n]
    rates = [round(0.05 * (15 / 0.05) ** (r / 99) * 65536 / 100) for r in range(100)]
    sine = [round(127 * math.sin(2 * math.pi * i / 256)) & 0xFF for i in range(256)]
    out = ["        .align 2", "rate_tab:"]
    for i in range(0, 100, 10): out.append("        .dw     " + ", ".join(str(v) for v in rates[i:i + 10]))
    out.append("sine_tab:")
    for i in range(0, 256, 16): out.append("        .db     " + ", ".join(str(v) for v in sine[i:i + 16]))
    out.append("blk_tab:")
    for k, (o, b) in enumerate(BLOCKS): out.append("        .db     %d, %d, %d, 0" % (o, b, bases[k + 1] - b))
    return "\n".join(out) + "\n"


def build(out):
    rom = bytearray(open(anim_build.ROM_IN, "rb").read())
    assert hashlib.md5(rom).hexdigest() == anim_build.STOCK_MD5, "needs the stock Ver1.03E dump"

    # 1. presets -> one INIT program; programs 0-99 all point at it
    rec = bytearray(rom[PRESETS + INIT_FROM * PRESET_LEN:][:PRESET_LEN])
    rec[9:27] = b"INIT".ljust(18)
    rom[PRESETS:PRESETS + PRESET_LEN] = rec
    rom[FREE0:FREE0_END] = b"\xff" * (FREE0_END - FREE0)
    patch(rom, 0x1890, "a5 ac 0c b3 18 24", "5c b3 18 00 00 00")   # prog_addr:    MOV.W #INIT,R4
    patch(rom, 0x18CB, "a5 a8 0c b3 18 20", "58 b3 18 00 00 00")   # prog_addr_dp: MOV.W #INIT,R0

    # 2. TEST mode (A + SYSTEM at power on) off: skip the second key-combo check
    patch(rom, 0x0256, "54 01 18 12", "13 00 05 e4")                 # PJMP @main_init

    # 3. ROSETTA code in the freed page-0 space
    src = open(os.path.join(ROOT, "src", "rosetta.asm"), encoding="utf-8").read()
    src += "\n" + open(os.path.join(ROOT, "src", "lfo.asm"), encoding="utf-8").read()
    src += "\n" + open(os.path.join(ROOT, "src", "inputtest.asm"), encoding="utf-8").read()
    src += "\n" + gen_tables(rom)
    a = Asm({"ROSETTA_BASE": ROSETTA_BASE, "BIG_COL": (38 - big_width(rom, "ROSETTA")) // 2})
    code = a.assemble(src)
    lo, hi = min(code), max(code)
    assert FREE0 <= lo and hi < FREE0_END, "rosetta code outside the free area"
    for k, v in code.items():
        assert rom[k] == 0xFF; rom[k] = v
    bad = a.check(bytes(rom), 0)
    assert not bad, bad
    sy = a.syms

    # 3a. page-1 stub so page-0 Rosetta can PJSR Sony's live-coefficient routine (0x156B3 ends in RTS, not PRTS)
    assert sy["CWRITE_P1"] == CWRITE_P1
    patch(rom, CWRITE_P1, "ff ff ff ff ff", "18 56 b3 11 19")        # JSR @0x56B3 ; PRTS

    # 3b. 5 ms system tick (vector 0xC0) -> lfo_isr, which then jumps on to Sony's handler
    patch(rom, 0x00C0, "00 00 04 fd", bytes([0, 0, sy["lfo_isr"] >> 8, sy["lfo_isr"] & 0xFF]))

    # 4. SYSTEM:Select: B (CLOCK) -> ROSET, C (MEMRY) -> nothing yet
    patch(rom, 0x4840, "49 d0", bytes([sy["ros_entry"] >> 8, sy["ros_entry"] & 0xFF]))
    patch(rom, 0x4842, "48 87", "48 29")                             # C -> stay on SYSTEM:Select
    patch(rom, 0x4801, b"CLOCK", b"ROSET")
    patch(rom, 0x4807, b"MEMRY", b"     ")

    # 5. Hex girl boot (page 1)
    px, durs = anim_build.load_frames(os.path.join(ROOT, "frames", "rosetta_boot.gif"))
    holds = [round(dd / 50.0) if dd else 1 for dd in durs]
    anim_build.build(px, holds, 0x4B, out, rom=rom, bootflag=True)
    final = bytearray(open(out, "rb").read())
    with open(out[:-4] + "_rosetta.lst", "w", encoding="utf-8") as f:
        f.write(a.listing(bytes(final), 0) + "\n")
    with open(out[:-4] + "_syms.txt", "w", encoding="utf-8") as f:
        for k, v in sorted(sy.items(), key=lambda kv: kv[1]):
            f.write("%05X %s\n" % (v, k))
    used = hi - lo + 1
    print("rosetta v%s: page-0 code %d B @%05X, page-0 free left %d B" % (VERSION, used, lo, FREE0_END - hi - 1))
    return final, sy


def emu_tour(img, shots):
    os.makedirs(shots, exist_ok=True)
    for f in os.listdir(shots):
        if f.endswith(".png"): os.remove(os.path.join(shots, f))
    e = gp5emu.Emu(img)
    e.run(1_500_000)
    out = []
    def snap(name):
        p = os.path.join(shots, name + ".png"); e.png(p); out.append((name, e.text().splitlines()))
    steps = [("SYSTEM", None), ("B", "01_root"), ("dial+1", "02_root_tempo"), ("dial+5", "03_root_about_scroll"),
             ("D", "04_boot_page"), ("EDIT", "05_boot_edit"), ("dial-1", "06_boot_off"), ("EXIT", "07_boot_edit_off"),
             ("EXIT", "08_back_root"), ("A", "09_mod"), ("dial+1", None), ("EDIT", "10_mod_rate_edit"),
             ("dial+7", "11_rate_27"), ("EXIT", None), ("dial+3", "12_mod_scrolled"), ("EXIT", None),
             ("E", "13_about"), ("EXIT", None), ("C", "15_input"), ("C", "15b_input_l_edit"), ("dial-1", "16_input_l_off"),
             ("EXIT", None), ("EXIT", None), ("EXIT", "14_back_to_system")]
    for k, name in steps:
        if k.startswith("dial"): e.dial(int(k[4:]), 150000)
        else: e.press(k)
        if name: snap(name)
    ram = bytes(e.ram)
    return out, ram


def emu_boot_skip(img, ram):
    """second power-on with HEX GIRL = OFF in battery RAM: the animation must be skipped"""
    e = gp5emu.Emu(img); e.ram[:] = ram
    assert ram[0x7000:0x7003] == b"RS\x00", "settings not saved: %r" % ram[0x7000:0x7003]
    e.run(400_000)
    return e.text().splitlines()[0]


def emu_input_test(img):
    """INPUT TEST: IN L off -> c(08) = 0 sent through Sony's routine; IN R off -> c(09) = 0; re-entering ROSETTA
    turns both back on (0x14300000). Returns the decoded live coefficient updates for c(08) / c(09)."""
    import dsplog
    e = gp5emu.Emu(img); e.run(1_500_000)
    e.dsplog = []
    for k in ("SYSTEM", "B", "C", "C"): e.press(k)        # ROSETTA -> INPUT -> IN L (row C), edit
    e.dial(-1, 150000)                                   # IN L -> OFF
    e.press("EXIT"); e.press("D"); e.dial(-1, 150000)    # IN R (row D), edit, OFF
    e.press("EXIT"); e.press("EXIT"); e.press("EXIT"); e.press("B")   # back to SYSTEM:Select, re-enter: both on
    tms, dsp2 = dsplog.decode(e.dsplog)
    ups = [(a, v) for a, v in tms.updates if a in (8, 9)]
    assert ups == [(8, 0), (9, 0x14300000), (8, 0), (9, 0), (8, 0x14300000), (9, 0x14300000)], ups
    return ups


if __name__ == "__main__":
    out = os.path.join(ROOT, "build", "hrgp5_rosetta_os.bin")
    img, sy = build(out)
    shots, ram = emu_tour(bytes(img), os.path.join(ROOT, "out", "shots"))
    for name, lines in shots:
        print("--", name)
    print("power-on with HEX GIRL off, top text row after 0.27 s:", repr(emu_boot_skip(bytes(img), ram)))
    print("INPUT TEST, c(08)/c(09) updates sent:", ["c(%02x)=%08X" % u for u in emu_input_test(bytes(img))])
    print("x4 md5", hashlib.md5(open(out[:-4] + "_SST39SF040_x4.bin", "rb").read()).hexdigest())
