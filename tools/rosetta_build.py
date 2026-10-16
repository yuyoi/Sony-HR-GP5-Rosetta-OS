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
VERSION = "0.1"
PRESETS, PRESET_LEN = 0xB318, 0xB0
INIT_FROM = 8                        # "Transparnt" becomes the single INIT program
FREE0, FREE0_END = PRESETS + PRESET_LEN, 0xF7D8
ROSETTA_BASE = 0xB3D0


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
    a = Asm({"ROSETTA_BASE": ROSETTA_BASE, "BIG_COL": (38 - big_width(rom, "ROSETTA")) // 2})
    code = a.assemble(src)
    lo, hi = min(code), max(code)
    assert FREE0 <= lo and hi < FREE0_END, "rosetta code outside the free area"
    for k, v in code.items():
        assert rom[k] == 0xFF; rom[k] = v
    bad = a.check(bytes(rom), 0)
    assert not bad, bad
    sy = a.syms

    # 4. SYSTEM:Select: B (CLOCK) -> ROSET, C (MEMRY) -> nothing yet
    patch(rom, 0x4840, "49 d0", bytes([sy["ros_entry"] >> 8, sy["ros_entry"] & 0xFF]))
    patch(rom, 0x4842, "48 87", "48 29")                             # C -> stay on SYSTEM:Select
    patch(rom, 0x4801, b"CLOCK", b"ROSET")
    patch(rom, 0x4807, b"MEMRY", b"     ")

    # 5. Hex girl boot (page 1)
    px, durs = anim_build.load_frames(os.path.join(ROOT, "frames", "rosetta_boot.gif"))
    holds = [round(dd / 50.0) if dd else 1 for dd in durs]
    anim_build.build(px, holds, 0x4B, out, rom=rom)
    final = bytearray(open(out, "rb").read())
    with open(out[:-4] + "_rosetta.lst", "w", encoding="utf-8") as f:
        f.write(a.listing(bytes(final), 0) + "\n")
    used = hi - lo + 1
    print("rosetta v%s: page-0 code %d B @%05X, page-0 free left %d B" % (VERSION, used, lo, FREE0_END - hi - 1))
    return final, sy


def emu_tour(img, shots):
    os.makedirs(shots, exist_ok=True)
    e = gp5emu.Emu(img)
    e.run(1_500_000)
    out = []
    def snap(name):
        p = os.path.join(shots, name + ".png"); e.png(p); out.append((name, e.text().splitlines()))
    snap("1_play")
    e.press("SYSTEM"); snap("2_system")
    e.press("B"); snap("3_rosetta")
    e.press("BYPASS"); snap("4_rosetta_bypass")
    e.press("EXIT"); snap("5_back_to_system")
    e.press("EXIT"); snap("6_play")
    return out


if __name__ == "__main__":
    out = os.path.join(ROOT, "build", "hrgp5_rosetta_os.bin")
    img, sy = build(out)
    for name, lines in emu_tour(bytes(img), os.path.join(ROOT, "out", "shots")):
        print("--", name); print("\n".join(l.rstrip(".") for l in (lines[0], lines[4])))
    print("x4 md5", hashlib.md5(open(out[:-4] + "_SST39SF040_x4.bin", "rb").read()).hexdigest())
