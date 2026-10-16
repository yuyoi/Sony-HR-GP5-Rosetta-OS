# Draw Rosetta UI mockups at the real 228x40 LCD resolution with the HR-GP5's own fonts and icons.
#   python -I tools/mockup.py dumps/hrgp5_eprom_read1.bin out/mockups.png
import sys, os, math
sys.path.insert(0, os.path.dirname(__file__))
from gp5lcd import W, H, to_png
from PIL import Image, ImageDraw


class Canvas:
    def __init__(s, rom):
        s.rom = rom; s.px = [[0] * W for _ in range(H)]

    def set(s, x, y, v=1, xor=False):
        if 0 <= x < W and 0 <= y < H:
            s.px[y][x] = (s.px[y][x] ^ v) if xor else v

    def text(s, x, y, t, xor=False):
        for ch in t:
            c = ord(ch)
            g = s.rom[0xA7C2 + (c & 0x7F) * 7:][:7]
            for r, v in enumerate(g):
                for i in range(6):
                    if (v >> (7 - i)) & 1: s.set(x + i, y + r, 1, xor)
            x += 6
        return x

    def big(s, x, y, t):
        for ch in t.encode():
            p = 0x10000 | ((s.rom[0x112F4 + (ch & 0x7F) * 2] << 8) | s.rom[0x112F5 + (ch & 0x7F) * 2])
            w = s.rom[p]; p += 1
            for col in range(w):
                for r in range(18):
                    v = s.rom[p + col * 18 + r]
                    for i in range(6):
                        if (v >> (7 - i)) & 1: s.set(x + col * 6 + i, y + r)
            x += w * 6
        return x

    def icon(s, x, y, n):
        p = 0x10000 | ((s.rom[0x10000 + n * 2] << 8) | s.rom[0x10001 + n * 2])
        w = s.rom[p]; p += 1
        for col in range(w):
            for r in range(16):
                v = s.rom[p + col * 16 + r]
                for i in range(6):
                    if (v >> (7 - i)) & 1: s.set(x + col * 6 + i, y + r)

    def rect(s, x, y, w, h, fill=False, xor=False):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if fill or yy in (y, y + h - 1) or xx in (x, x + w - 1): s.set(xx, yy, 1, xor)

    def hline(s, x, y, w):
        for xx in range(x, x + w): s.set(xx, y)

    def key(s, slot, label, on=True):
        """soft-key label above A-F (6 slots of 38 px), inverse when on"""
        x = 1 + slot * 38
        if on: s.rect(x, 32, 36, 8, fill=True)
        s.text(x + (36 - 6 * len(label)) // 2, 33, label, xor=on)

    def bar(s, x, y, w, frac):
        s.rect(x, y, w, 5)
        s.rect(x + 1, y + 1, max(0, int((w - 2) * frac)), 3, fill=True)


def screens(rom):
    out = []
    # 1. PLAY / home
    c = Canvas(rom)
    c.text(0, 0, "P101"); c.text(30, 0, "ROSETTA"); c.text(150, 0, "120 BPM"); c.text(204, 0, "\x86")
    c.hline(0, 8, 228)
    c.big(2, 11, "Hex Crunch")
    for i, (lab, on) in enumerate([("COMP", 1), ("DIST", 1), ("EQ", 1), ("CHOR", 0), ("DLY", 1), ("HALL", 1)]):
        c.key(i, lab, bool(on))
    out.append(("1 PLAY: big name, A-F = block on/off, dial = program", c))
    # 2. block edit
    c = Canvas(rom)
    c.text(0, 0, "DIST > Hex Crunch"); c.text(186, 0, "1/2")
    c.hline(0, 8, 228)
    for i, (n, v, f) in enumerate([("DRIVE", "72", .72), ("TONE", "48", .48), ("LEVEL", "60", .6)]):
        y = 10 + i * 7
        c.text(2, y, n); c.text(48, y, v); c.bar(66, y + 1, 90, f)
        if i == 0: c.rect(0, y, 160, 7, fill=True, xor=True)
    c.text(166, 10, "LFO 1"); c.text(166, 17, "> DRIVE"); c.text(166, 24, "1/4 TRI")
    for i, lab in enumerate(["DRIVE", "TONE", "LEVEL", "TYPE", "LFO", "BYP"]): c.key(i, lab, i == 0)
    out.append(("2 EDIT: params with bars, A-F pick, dial sets, LFO assign", c))
    # 3. ROSETTA hub
    c = Canvas(rom)
    c.text(0, 0, "ROSETTA OS"); c.text(186, 0, "v0.2")
    c.hline(0, 8, 228)
    c.text(4, 13, "Hex girl inside"); c.text(4, 21, "4 LFO  TAP  MIDI CLK")
    c.icon(170, 11, 0)
    for i, lab in enumerate(["MOD", "TEMPO", "LAB", "MIDI", "BOOT", "INFO"]): c.key(i, lab, False)
    out.append(("3 ROSETTA hub: A-F open the Rosetta pages", c))
    # 4. MOD / LFO page
    c = Canvas(rom)
    c.text(0, 0, "MOD 1  LFO"); c.text(150, 0, "> DLY TIME")
    c.hline(0, 8, 228)
    for x in range(110):
        y = 20 + int(8 * math.sin(x / 110 * 2 * math.pi * 2))
        c.set(4 + x, y)
    c.text(122, 10, "RATE  1/8"); c.text(122, 17, "DEPTH 35"); c.text(122, 24, "SYNC  ON")
    for i, lab in enumerate(["WAVE", "RATE", "DEPTH", "DEST", "SYNC", "ON"]): c.key(i, lab, i == 5)
    out.append(("4 MOD: LFO with live wave, tempo sync, any destination", c))
    return out


if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    out = sys.argv[2]
    shots = screens(rom)
    ims = []
    for title, c in shots:
        tmp = out + ".tmp.png"; to_png(c.px, tmp, 3); ims.append((title, Image.open(tmp).copy())); os.remove(tmp)
    w = ims[0][1].width; h = ims[0][1].height
    sheet = Image.new("RGB", (w, (h + 16) * len(ims)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(ims):
        d.text((6, i * (h + 16) + 2), t, fill=(240, 240, 240)); sheet.paste(im, (0, i * (h + 16) + 15))
    sheet.save(out); print("wrote", out, sheet.size)
