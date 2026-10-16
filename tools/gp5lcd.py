# HR-GP5 LCD model: SED1330-class, 228x40, graphics layer (38 B/row, 6 px per byte,
# MSB first, bits 1-0 unused) XOR text layer (38x5 chars, 6x8 cells).
# Renders the stock boot screen by re-doing what sub_02558 does, using the ROM's fonts.
import sys
from PIL import Image

W, H, CR = 228, 40, 38
GFX, TXT, END = 0x000, 0x5F0, 0x6AE


class Screen:
    def __init__(s, rom):
        s.rom = rom
        s.ram = bytearray(END)

    def r16(s, a): return (s.rom[a] << 8) | s.rom[a + 1]

    def clear(s):  # sub_01021
        s.ram[:] = bytes(END)
        for a in range(0x130, 0x156): s.ram[a] = 0xFC

    def text(s, off, a):  # sub_01ABD: copy C string into RAM (text layer)
        while True:
            c = s.rom[a]; s.ram[off] = c; off += 1; a += 1
            if c == 0: return

    def bigglyph(s, off, c, table, rows):  # sub_010D7 / sub_0110B (page-1 tables)
        p = 0x10000 | s.r16(0x10000 | (table + (c & 0x7F) * 2))
        w = s.rom[p]; p += 1
        for _ in range(w):
            for r in range(rows): s.ram[off + r * CR] = s.rom[p]; p += 1
            off += 1
        return off

    def bigstr(s, off, a):  # sub_010B1
        while s.rom[a]:
            off = s.bigglyph(off, s.rom[a], 0x12F4, 0x12); a += 1

    def gchar(s, off, c):  # sub_0115E: small text into graphics layer
        if c >= 0xA0: c = 0x20
        if c >= 0x80: base, n = 0xAB42 + (c & 0x7F) * 8, 8
        else: base, n = 0xA7C2 + c * 7, 7
        for r in range(n): s.ram[off + r * CR] = s.rom[base + r]
        return off + 1

    def gstr(s, off, a):  # sub_01147 (R2 advances)
        while s.rom[a]:
            off = s.gchar(off, s.rom[a]); a += 1
        return off

    def boot(s):  # sub_02558
        s.clear()
        s.text(0x5FB, 0xAC92)
        s.bigglyph(0x1C8 + 7, s.rom[0xACCD], 0x0000, 0x10)
        s.bigstr(0x1C8 + 0x0D, 0xACA3)
        o = s.gstr(0x339, 0xAC88)
        s.gstr(o, 0xB316)
        s.text(0x68A, 0xACAA)

    def pixels(s):
        px = [[0] * W for _ in range(H)]
        for y in range(H):
            for b in range(CR):
                v = s.ram[GFX + y * CR + b]
                for x in range(6): px[y][b * 6 + x] = (v >> (7 - x)) & 1
        # text layer: internal CG ROM approximated with the ROM's own 7-row font
        for row in range(5):
            for col in range(CR):
                c = s.ram[TXT + row * CR + col]
                if c == 0: continue
                if 0x80 <= c < 0xA0: g = s.rom[0xAB42 + (c & 0x7F) * 8:][:8]
                else: g = s.rom[0xA7C2 + (c & 0x7F) * 7:][:7]
                for r, v in enumerate(g):
                    for x in range(6): px[row * 8 + r][col * 6 + x] ^= (v >> (7 - x)) & 1
        return px


def to_png(px, path, scale=4):
    on, off = (20, 30, 60), (150, 200, 230)
    im = Image.new("RGB", (W, H))
    im.putdata([on if px[y][x] else off for y in range(H) for x in range(W)])
    im.resize((W * scale, H * scale), Image.NEAREST).save(path)


if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    s = Screen(rom)
    s.boot()
    to_png(s.pixels(), sys.argv[2])
    print("wrote", sys.argv[2])
