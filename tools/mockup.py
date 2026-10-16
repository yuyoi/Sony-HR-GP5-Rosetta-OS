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

    def text(s, x, y, t, xor=False, val=1):
        for ch in t:
            c = ord(ch)
            g = s.rom[0xA7C2 + (c & 0x7F) * 7:][:7]
            for r, v in enumerate(g):
                for i in range(6):
                    if (v >> (7 - i)) & 1: s.set(x + i, y + r, val, xor)
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


# ---------------- Classic Mac house style (same as the SP-808 Rosetta OS) ----------------
def mac_window(c, title):
    """frame, pinstriped title bar with close box, title centred in a cleared gap, separator"""
    c.rect(0, 0, W, H)
    for y in (2, 4, 6, 8):
        c.hline(2, y, W - 4)
    tw = len(title) * 6 + 2                     # pseudo-bold title
    tx = (W - tw) // 2
    for yy in range(1, 10):
        for xx in range(tx - 4, tx + tw + 4): c.set(xx, yy, 0)
        for xx in range(4, 17): c.set(xx, yy, 0)
    c.rect(6, 2, 9, 7)                          # close box
    c.text(tx, 2, title); c.text(tx + 1, 2, title)
    c.hline(1, 10, W - 2)


def mac_rows(c, rows, sel=0, top=0, total=None, arrows=False):
    """3 visible rows: (label, value); value right-aligned; selection inverted; scroll bar if more"""
    total = total or len(rows)
    for i, (lab, val) in enumerate(rows[top:top + 3]):
        y = 11 + i * 9
        c.text(4, y + 1, lab)
        v = ">" if arrows else val
        c.text(212 - 6 * len(v), y + 1, v)
        if top + i == sel: c.rect(1, y, 216, 9, fill=True, xor=True)
    if total > 3:                               # scroll bar
        c.rect(218, 11, 9, 28)
        th = max(5, 26 * 3 // total); ty = 12 + (26 - th) * top // max(1, total - 3)
        c.rect(220, ty, 5, th, fill=True)
    else:
        for y in range(11, 39): c.set(218, y)


def screens_mac(rom):
    out = []
    c = Canvas(rom); mac_window(c, "ROSETTA OS")
    mac_rows(c, [("MODULATION", ""), ("TEMPO", ""), ("LAB", ""), ("MIDI", ""), ("BOOT", ""), ("ABOUT", "")], sel=0, arrows=True)
    out.append(("1 ROSETTA menu: dial moves, EDIT/PAGE or A-F opens (A-F = rows 1-6)", c))
    c = Canvas(rom); mac_window(c, "MOD: LFO 1")
    mac_rows(c, [("WAVE", "TRIANGLE"), ("RATE", "1/8"), ("DEPTH", "35"), ("DEST", "DLY TIME"), ("SYNC", "ON")], sel=1)
    out.append(("2 MOD page: dial selects, EDIT/PAGE = edit value with the dial, EXIT back", c))
    c = Canvas(rom); mac_window(c, "DIST: HEX CRUNCH")
    mac_rows(c, [("DRIVE", "72"), ("TONE", "48"), ("LEVEL", "60"), ("TYPE", "DIST2")], sel=0, top=0)
    out.append(("3 Effect edit: one window per block, 3 rows + scroll", c))
    c = Canvas(rom); mac_window(c, "P101  120 BPM")
    c.big(4, 12, "Hex Crunch")
    c.text(4, 31, "COMP DIST EQ DLY HALL"); c.text(164, 31, "-CHOR")
    for y in range(11, 39): c.set(218, y)
    out.append(("4 PLAY in the same style: big name, chain line (off blocks with -)", c))
    return out


def topbar(c, style, title, right=""):
    """title-bar variants for the Mac window (rows start at y 11 in every variant)"""
    c.rect(0, 0, W, H)
    bold = lambda x, y, t: (c.text(x, y, t), c.text(x + 1, y, t))
    tw = len(title) * 6 + 1
    if style == "pinstripe":
        mac_window(c, title); return
    if style == "inverse":                       # solid black bar, white bold title (System 1 menu-bar feel)
        c.rect(1, 1, W - 2, 9, fill=True)
        x = (W - tw) // 2
        c.text(x, 2, title, val=0); c.text(x + 1, 2, title, val=0)
        if right: c.text(W - 4 - 6 * len(right), 2, right, val=0)
    elif style == "underline":                   # plain bold title left, status right, one rule
        bold(4, 2, title)
        if right: c.text(W - 4 - 6 * len(right), 2, right)
        c.hline(1, 10, W - 2)
    elif style == "double":                      # dialog style: title, double rule
        bold((W - tw) // 2, 1, title)
        c.hline(1, 9, W - 2); c.hline(1, 10, W - 2)
    elif style == "dither":                      # 50% desktop pattern with a cleared title box
        for y in range(1, 10):
            for x in range(1, W - 1):
                if (x + y) & 1: c.set(x, y)
        x = (W - tw) // 2
        for y in range(1, 10):
            for xx in range(x - 4, x + tw + 4): c.set(xx, y, 0)
        c.rect(x - 4, 1, tw + 8, 9)
        bold(x, 2, title)
    elif style == "menubar":                     # old Mac menu bar: apple-ish mark + menus, current one inverted
        c.hline(1, 10, W - 2)
        for (yy, a, b) in ((2, 5, 7), (3, 4, 9), (4, 3, 10), (5, 3, 10), (6, 3, 10), (7, 4, 9), (8, 5, 8)):
            for xx in range(a, b): c.set(xx, yy)
        c.set(7, 1); c.set(8, 1)
        x = 16
        for i, m in enumerate(title.split()):
            w = len(m) * 6 + 6
            if i == 1: c.rect(x - 3, 1, w, 9, fill=True)
            v = 0 if i == 1 else 1
            c.text(x, 2, m, val=v); c.text(x + 1, 2, m, val=v)
            x += w + 2
    elif style == "tab":                         # folder tab with the title, baseline across
        c.rect(1, 1, tw + 10, 10, fill=True)
        c.text(6, 2, title, val=0); c.text(7, 2, title, val=0)
        if right: c.text(W - 4 - 6 * len(right), 2, right)
        c.hline(1, 10, W - 2)
    else: raise ValueError(style)


def screens_topbars(rom):
    rows = [("MODULATION", ""), ("TEMPO", ""), ("LAB", ""), ("MIDI", ""), ("BOOT", ""), ("ABOUT", "")]
    out = []
    for name, style, title, right in (
            ("A pinstripes (SP-808)", "pinstripe", "ROSETTA OS", ""),
            ("B solid black bar", "inverse", "ROSETTA OS", "v0.2"),
            ("C plain + rule, status right", "underline", "ROSETTA OS", "120 BPM"),
            ("D dialog double rule", "double", "ROSETTA OS", ""),
            ("E dither pattern + title box", "dither", "ROSETTA OS", ""),
            ("F old Mac menu bar", "menubar", "Rosetta Mod Tempo Lab", ""),
            ("G folder tab", "tab", "ROSETTA OS", "120 BPM")):
        c = Canvas(rom); topbar(c, style, title, right)
        mac_rows(c, rows, sel=0, arrows=True)
        out.append((name, c))
    return out
