# HR-GP5 boot animation builder.
#   python -I tools/anim_build.py <frames.gif | frames_dir> [--hold N] [--tick N] [--out build/x.bin]
# Frames: 228x40, dark pixel = LCD dot on. Each frame is delta-coded against the previous one (codec2.py),
# a small H8/500 player in page-1 free space plays them, then the stock boot screen draws.
import sys, os, re, hashlib, argparse
sys.path.insert(0, os.path.dirname(__file__))
from h8500dis import Dis
from gp5lcd import W, H, CR, Screen, to_png
from PIL import Image, ImageSequence

ROM_IN = os.path.join(os.path.dirname(__file__), "..", "dumps", "hrgp5_eprom_read1.bin")
STOCK_MD5 = "4db502640e1a799b2114d42262dbdff9"
HOOK = 0x2558                       # sub_02558 = boot screen, only caller 0x0063E
HOOK_ORIG = bytes.fromhex("18 10 21 04 04 8d")  # JSR @H'1021 ; LDC.B #4,DP
PLAYER = 0x1F5A0                    # page-1 free space (FF from 0x1F596)
LIMIT = 0x1FFFF                     # keep 0x1FFFF (test-mode checksum byte) untouched
GBYTES = CR * H                     # 1520 bytes graphics layer


# ---------- frames ----------
def load_frames(src, invert=False):
    ims, durs = [], []
    if os.path.isdir(src):
        for f in sorted(os.listdir(src)):
            if f.lower().endswith((".png", ".bmp", ".gif")):
                ims.append(Image.open(os.path.join(src, f)).convert("L")); durs.append(None)
    else:
        im = Image.open(src)
        for fr in ImageSequence.Iterator(im):
            ims.append(fr.convert("L")); durs.append(fr.info.get("duration"))
    out = []
    for im in ims:
        if im.size != (W, H):
            sys.exit("frame is %dx%d, must be %dx%d" % (im.size + (W, H)))
        px = im.load()
        out.append([[(px[x, y] < 128) ^ invert for x in range(W)] for y in range(H)])
    return out, durs


def pack(px):
    b = bytearray(GBYTES)
    for y in range(H):
        for c in range(CR):
            v = 0
            for x in range(6): v |= px[y][c * 6 + x] << (7 - x)
            b[y * CR + c] = v
    return bytes(b)


import codec2


# ---------- player (hand-assembled H8/500, page 1) ----------
PROBE_P0, PROBE_P1 = 0x0FA00, 0x1FFFE      # markers: page 0 tail 'O'; page 1 '1' (A17=0 copies) / '3' (A17=1 copies)


def probe_src(hi, lo, tick):
    """after the animation: show [page0 FA00][page1 FFFE][page3 FFFE] on text row 0 for ~2 s.
    'O1x': page-0 tail is ROM. x = '3': A17 reaches the chip (pages 2-3 usable), '1': it doesn't (mirror)."""
    return [
        ("raw", "04 00 8c"),            # LDC.B #0,EP
        ("raw", "5c %02x %02x" % (hi(PROBE_P0), lo(PROBE_P0))),  # MOV.W #FA00,R4
        ("raw", "d4 81"),               # MOV.B @R4,R1
        ("raw", "04 01 8c"),            # LDC.B #1,EP
        ("raw", "5c %02x %02x" % (hi(PROBE_P1), lo(PROBE_P1))),  # MOV.W #FFFE,R4
        ("raw", "d4 82"),               # MOV.B @R4,R2
        ("raw", "04 03 8c"),            # LDC.B #3,EP
        ("raw", "d4 83"),               # MOV.B @R4,R3   (blit puts EP back to 1)
        ("raw", "58 05 f0"),            # MOV.W #H'05F0,R0  text row 0, col 0
        ("raw", "c0 91"),               # MOV.B R1,@R0+
        ("raw", "c0 92"),               # MOV.B R2,@R0+
        ("raw", "c0 93"),               # MOV.B R3,@R0+
        ("br", 0x1e, "blit"),
        ("raw", "55 28"),               # MOV.B #40,R5   ~2 s
        ("lbl", "p0"),
        ("ptick", "5b %02x %02x" % (hi(tick), lo(tick))),
        ("lbl", "p1"),
        ("pinner", "5a 01 90"),
        ("lbl", "p2"),
        ("raw", "0c 00 01 32"), ("br", 0x26, "p2"),
        ("raw", "0c 00 01 33"), ("br", 0x26, "p1"),
        ("raw", "04 01 35"), ("br", 0x26, "p0"),
    ]


def assemble(base, data_addr, tick, probe=False, bootflag=False, data_page=1):
    hi, lo = lambda v: (v >> 8) & 0xFF, lambda v: v & 0xFF
    ep = "04 %02x 8c" % data_page       # LDC.B #page,EP (frame data page)
    src = [
        ("raw", "12 3f"),               # STM.W (R0-R5),@-SP
        ("raw", "bf 98"),               # STC.W SR,@-SP
        ("raw", "0c 07 00 48"),         # ORC.W #H'0700,SR  mask IRQs: IRQ0 LCD stream must not fight our blits
        ("raw", "04 04 8d"),            # LDC.B #4,DP   (R0-R3 -> page 4 RAM)
        ("raw", ep),                    # LDC.B #page,EP   (R4-R5 -> the frame data page)
    ] + ([
        ("raw", "1d 70 00 05 52 53"),   # CMP.W #'RS',@0x7000   Rosetta settings valid?
        ("br", 0x26, "play"),           # BNE play              (no: default = play)
        ("raw", "15 70 02 04 00"),      # CMP.B #0,@0x7002      HEX GIRL = OFF?
        ("br16", 0x37, "done"),         # BEQ done              (skip the animation)
        ("lbl", "play"),
    ] if bootflag else []) + [
        ("br", 0x1e, "clrz"),           # BSR clrz
        ("raw", "5c %02x %02x" % (hi(data_addr), lo(data_addr))),  # MOV.W #data,R4
        ("lbl", "frame"),
        ("raw", "c4 85"),               # MOV.B @R4+,R5   hold (0 = end, bit 7 = replay)
        ("br", 0x27, "done"),
        ("raw", "a5 f7"),               # BTST.B #7,R5
        ("br", 0x27, "plain"),
        ("raw", "04 7f 55"),            # AND.B #H'7F,R5
        ("raw", "c4 81"),               # MOV.B @R4+,R1   replay address hi
        ("raw", "a1 10"),               # SWAP.B R1
        ("raw", "c4 81"),               # MOV.B @R4+,R1   lo
        ("raw", "bf 94"),               # MOV.W R4,@-SP
        ("raw", "a9 84"),               # MOV.W R1,R4
        ("br", 0x1e, "decode"),
        ("raw", "cf 84"),               # MOV.W @SP+,R4
        ("br", 0x20, "show"),
        ("lbl", "plain"),
        ("br", 0x1e, "decode"),
        ("lbl", "show"),
        ("br", 0x1e, "blit"),           # BSR blit   frame -> LCD directly
        ("lbl", "hold"),
        ("tick", "5b %02x %02x" % (hi(tick), lo(tick))),  # MOV.W #tick,R3
        ("lbl", "t1"),
        ("inner", "5a 01 90"),          # MOV.W #H'0190,R2 (same unit as stock sub_019E1)
        ("lbl", "t2"),
        ("raw", "0c 00 01 32"),         # SUB.W #1,R2
        ("br", 0x26, "t2"),
        ("raw", "0c 00 01 33"),         # SUB.W #1,R3
        ("br", 0x26, "t1"),
        ("raw", "04 01 35"),            # SUB.B #1,R5
        ("br", 0x26, "hold"),
        ("br", 0x20, "frame"),
        ("lbl", "done"),
    ] + (probe_src(hi, lo, tick) if probe else []) + [
        ("br", 0x1e, "clr"),            # BSR clr  (what the replaced JSR @H'1021 did)
        ("br", 0x1e, "lcdrst"),         # BSR lcdrst (hand the LCD back to the IRQ0 streamer)
        ("raw", "cf 88"),               # LDC.W @SP+,SR
        ("raw", "02 3f"),               # LDM.W @SP+,(R0-R5)
        ("raw", "04 04 8d"),            # LDC.B #4,DP  (replaced instruction)
        ("raw", "11 19"),               # PRTS
        # decode one frame: tokens @R4 (EP) applied in place to RAM 0x40000 (DP), format in codec2.py
        ("lbl", "decode"),
        ("raw", "a8 13"),               # CLR.W R0
        ("lbl", "tok"),
        ("raw", "48 05 f0"),            # CMP.W #H'05F0,R0
        ("br", 0x24, "dret"),           # BCC dret
        ("raw", "c4 81"),               # MOV.B @R4+,R1   token
        ("raw", "a1 82"),               # MOV.B R1,R2
        ("raw", "a2 12"),               # EXTU.B R2
        ("raw", "a1 f7"),               # BTST.B #7,R1
        ("br", 0x26, "hi80"),
        ("raw", "a1 f6"),               # BTST.B #6,R1
        ("br", 0x26, "hi40"),
        ("raw", "a1 f5"),               # BTST.B #5,R1
        ("br", 0x26, "skipl"),
        ("raw", "aa 08"),               # ADD.W #1,R2     00-1F skip n+1
        ("lbl", "skip"),
        ("raw", "aa 20"),               # ADD.W R2,R0
        ("br", 0x20, "tok"),
        ("lbl", "skipl"),
        ("raw", "41 3f"),               # CMP.B #H'3F,R1  3F = end of frame
        ("br", 0x27, "dret"),
        ("raw", "04 1f 52"),            # AND.B #H'1F,R2  20-3E skip (n+1)*32
        ("raw", "aa 08"),               # ADD.W #1,R2
        ("raw", "aa 1a"), ("raw", "aa 1a"), ("raw", "aa 1a"), ("raw", "aa 1a"), ("raw", "aa 1a"),  # SHLL.W R2 x5
        ("br", 0x20, "skip"),
        ("lbl", "hi40"),
        ("raw", "a1 f5"),               # BTST.B #5,R1
        ("br", 0x26, "copyl"),
        ("raw", "04 1f 52"),            # AND.B #H'1F,R2  40-5F fill n+1
        ("raw", "aa 08"),               # ADD.W #1,R2
        ("raw", "c4 83"),               # MOV.B @R4+,R3
        ("lbl", "floop"),
        ("raw", "c0 93"),               # MOV.B R3,@R0+
        ("raw", "0c 00 01 32"),         # SUB.W #1,R2
        ("br", 0x26, "floop"),
        ("br", 0x20, "tok"),
        ("lbl", "copyl"),
        ("raw", "04 1f 52"),            # AND.B #H'1F,R2  60-7F copy long
        ("raw", "a2 10"),               # SWAP.B R2
        ("raw", "c4 82"),               # MOV.B @R4+,R2   length lo
        ("raw", "aa 08"),               # ADD.W #1,R2
        ("raw", "c4 83"),               # MOV.B @R4+,R3   offset
        ("raw", "a3 12"),               # EXTU.B R3
        ("br", 0x20, "copy"),
        ("lbl", "hi80"),
        ("raw", "04 3f 52"),            # AND.B #H'3F,R2
        ("raw", "aa 08"),               # ADD.W #1,R2
        ("raw", "a1 f6"),               # BTST.B #6,R1
        ("br", 0x26, "copy1"),
        ("lbl", "xl"),                  # 80-BF XOR literals
        ("raw", "c4 83"),               # MOV.B @R4+,R3
        ("raw", "d0 63"),               # XOR.B @R0,R3
        ("raw", "c0 93"),               # MOV.B R3,@R0+
        ("raw", "0c 00 01 32"),         # SUB.W #1,R2
        ("br", 0x26, "xl"),
        ("br", 0x20, "tok"),
        ("lbl", "copy1"),               # C0-FF copy from pos+1
        ("raw", "5b 00 01"),            # MOV.W #1,R3
        ("lbl", "copy"),
        ("raw", "a8 23"),               # ADD.W R0,R3     R3 = source (ahead of R0)
        ("lbl", "cl"),
        ("raw", "c3 81"),               # MOV.B @R3+,R1
        ("raw", "c0 91"),               # MOV.B R1,@R0+
        ("raw", "0c 00 01 32"),         # SUB.W #1,R2
        ("br", 0x26, "cl"),
        ("br", 0x20, "tok"),
        ("lbl", "dret"),
        ("raw", "19"),                  # RTS
        ("lbl", "blit"),                # RAM 0x40000-0x406AD -> SED1330 VRAM 0
        ("raw", "bf 95"),               # MOV.W R5,@-SP
        ("raw", "04 00 8d"),            # LDC.B #0,DP  (@aa:16 -> page 0 I/O)
        ("raw", "04 04 8c"),            # LDC.B #4,EP  (R5 -> page 4 RAM)
        ("br", 0x1e, "curs0"),
        ("raw", "ad 13"),               # CLR.W R5
        ("lbl", "b1"),
        ("raw", "c5 83"),               # MOV.B @R5+,R3
        ("raw", "15 fd 30 93"),         # MOV.B R3,@H'FD30:16
        ("raw", "4d 06 ae"),            # CMP.W #H'06AE,R5
        ("br", 0x26, "b1"),
        ("raw", ep),                    # LDC.B #page,EP
        ("raw", "04 04 8d"),            # LDC.B #4,DP
        ("raw", "cf 85"),               # MOV.W @SP+,R5
        ("raw", "19"),                  # RTS
        ("lbl", "lcdrst"),              # = stock sub_00F2B
        ("raw", "04 00 8d"),            # LDC.B #0,DP
        ("raw", "1d fd d8 13"),         # CLR.W @H'FDD8:16  stream position
        ("raw", "15 fd da 13"),         # CLR.B @H'FDDA:16
        ("br", 0x1e, "curs0"),
        ("raw", "04 04 8d"),            # LDC.B #4,DP
        ("raw", "19"),                  # RTS
        ("lbl", "curs0"),               # CSRW 0, CSRDIR right, MWRITE (DP=0)
        ("raw", "15 fd 31 06 46"),      # MOV.B #H'46,@H'FD31:16
        ("raw", "15 fd 30 06 00"),      # MOV.B #H'00,@H'FD30:16
        ("raw", "15 fd 30 06 00"),      # MOV.B #H'00,@H'FD30:16
        ("raw", "15 fd 31 06 4c"),      # MOV.B #H'4C,@H'FD31:16
        ("raw", "15 fd 31 06 42"),      # MOV.B #H'42,@H'FD31:16
        ("raw", "19"),                  # RTS
        ("lbl", "clr"),
        ("br", 0x1e, "clrz"),
        ("raw", "58 01 30"),            # MOV.W #H'0130,R0
        ("lbl", "c2"),
        ("raw", "c0 06 fc"),            # MOV.B #H'FC,@R0+  (title underline, as stock)
        ("raw", "48 01 56"),            # CMP.W #H'0156,R0
        ("br", 0x26, "c2"),
        ("raw", "19"),                  # RTS
        ("lbl", "clrz"),
        ("raw", "a8 13"),               # CLR.W R0
        ("lbl", "c1"),
        ("raw", "c0 13"),               # CLR.B @R0+
        ("raw", "48 06 ae"),            # CMP.W #H'06AE,R0
        ("br", 0x26, "c1"),
        ("raw", "19"),                  # RTS
    ]
    labels, marks, pc = {}, {}, base
    for it in src:
        if it[0] == "lbl": labels[it[1]] = pc
        elif it[0] == "br": pc += 3 if it[1] == 0x1e else 2
        elif it[0] == "br16": pc += 3
        else:
            marks[it[0]] = pc; pc += len(bytes.fromhex(it[1]))
    code, pc = bytearray(), base
    for it in src:
        if it[0] == "lbl": continue
        if it[0] == "br16":                   # Bcc disp16
            d = labels[it[2]] - (pc + 3)
            code += bytes([it[1], (d >> 8) & 0xFF, d & 0xFF]); pc += 3
        elif it[0] == "br" and it[1] == 0x1e:   # BSR disp16
            d = labels[it[2]] - (pc + 3)
            code += bytes([0x1e, (d >> 8) & 0xFF, d & 0xFF]); pc += 3
        elif it[0] == "br":
            d = labels[it[2]] - (pc + 2)
            assert -128 <= d <= 127, it
            code += bytes([it[1], d & 0xFF]); pc += 2
        else:
            b = bytes.fromhex(it[1]); code += b; pc += len(b)
    return bytes(code), labels, marks


# ---------- tiny emulator for the player (checks the hand assembly) ----------
def emulate(rom, entry, labels):
    d = Dis(rom)
    R, ram, stack = [0] * 8, bytearray(0x10000), []
    ram[:0x800] = bytes(range(256)) * 8          # junk, so missed clears show up
    page = {"DP": 0, "EP": 0}
    Z = C = 0
    pc, frames, steps, last = entry, [], 0, ""
    sr = [0x0000]
    lcd = {"cmd": None, "args": [], "cur": 0, "vram": bytearray(0x800), "io": {}}

    def rd(r, inc):  # @Rn / @Rn+ read, R0-R3 via DP, R4-R5 via EP
        a, pg = R[r], page["DP"] if r < 4 else page["EP"]
        v = ram[a] if pg == 4 else rom[(pg << 16) | a]
        if inc: R[r] = (a + 1) & 0xFFFF
        return v

    def io(a, v):  # @aa:16 write with DP=0
        assert page["DP"] == 0, "I/O write with DP!=0"
        if a in (0xFD30, 0xFD31): assert sr[0] & 0x0700 == 0x0700, "LCD write with IRQs open"
        if a == 0xFD31: lcd["cmd"], lcd["args"] = v, []
        elif a == 0xFD30:
            if lcd["cmd"] == 0x46:
                lcd["args"].append(v)
                if len(lcd["args"]) == 2: lcd["cur"] = lcd["args"][0] | lcd["args"][1] << 8
            elif lcd["cmd"] == 0x42:
                lcd["vram"][lcd["cur"]] = v; lcd["cur"] += 1
        else: lcd["io"][a] = v

    def wr(r, v):
        assert r < 4 and page["DP"] == 4
        ram[R[r]] = v & 0xFF; R[r] = (R[r] + 1) & 0xFFFF

    while True:
        steps += 1
        assert steps < 5_000_000, "runaway"
        if pc == labels["hold"] and last == "RTS":   # just back from blit
            assert lcd["vram"][:0x6AE] == ram[:0x6AE], "VRAM != RAM after blit"
            frames.append(bytes(lcd["vram"][:GBYTES]))
        i = d.dec(pc); t = i.text; nxt = pc + i.len; last = t
        rn = lambda s: int(s[1]) if s[0] == "R" else None
        m = re.match(r"(\S+) ?(.*)", t); op, args = m.group(1), [a.strip() for a in m.group(2).split(",")] if m.group(2) else []
        imm = lambda s: int(s[3:], 16)
        absa = re.match(r"@H'([0-9A-F]{4}):16", args[-1]) if args else None
        if op == "STM.W": stack.append(list(R))
        elif op == "LDM.W": R[:6] = stack.pop()[:6]
        elif t == "STC.W SR, @-SP": stack.append(sr[0])
        elif t == "LDC.W @SP+, SR": sr[0] = stack.pop()
        elif op == "ORC.W" and args[1] == "SR": sr[0] |= imm(args[0])
        elif op == "MOV.W" and args[1] == "@-SP": stack.append(R[rn(args[0])])
        elif op == "MOV.W" and args[0] == "@SP+": R[rn(args[1])] = stack.pop()
        elif absa and op in ("MOV.B", "CLR.B", "CLR.W"):
            a = int(absa.group(1), 16)
            v = 0 if op.startswith("CLR") else imm(args[0]) if args[0].startswith("#") else R[rn(args[0])] & 0xFF
            io(a, v)
        elif op == "LDC.B": page[args[1]] = imm(args[0])
        elif op in ("BSR",): stack.append(nxt); nxt = i.target
        elif op == "RTS": nxt = (pc & ~0xFFFF) | stack.pop()
        elif op == "PRTS": return frames, R, page, ram, sr[0], lcd, stack
        elif op == "MOV.W" and args[0].startswith("#"): R[rn(args[1])] = imm(args[0]); Z = R[rn(args[1])] == 0
        elif op == "MOV.B" and args[0].startswith("@R"):
            v = rd(int(args[0][2]), args[0].endswith("+")); R[rn(args[1])] = (R[rn(args[1])] & 0xFF00) | v; Z = v == 0
        elif op == "MOV.B" and args[1].startswith("@R"):
            src = imm(args[0]) if args[0].startswith("#") else R[rn(args[0])] & 0xFF
            wr(int(args[1][2]), src)
        elif op == "MOV.B" and args[0].startswith("#"):
            r = rn(args[1]); R[r] = (R[r] & 0xFF00) | imm(args[0]); Z = imm(args[0]) == 0
        elif op == "MOV.B": R[rn(args[1])] = (R[rn(args[1])] & 0xFF00) | (R[rn(args[0])] & 0xFF)
        elif op == "MOV.W" and rn(args[0]) is not None and rn(args[1]) is not None: R[rn(args[1])] = R[rn(args[0])]
        elif op == "SWAP.B": r = rn(args[0]); R[r] = ((R[r] << 8) | (R[r] >> 8)) & 0xFFFF
        elif op == "SHLL.W": r = rn(args[0]); R[r] = (R[r] << 1) & 0xFFFF
        elif op == "XOR.B" and args[0] == "@R0": r = rn(args[1]); R[r] = (R[r] & 0xFF00) | ((R[r] ^ rd(0, False)) & 0xFF)
        elif op == "CMP.B" and args[0].startswith("#"): a, b = R[rn(args[1])] & 0xFF, imm(args[0]); Z, C = a == b, a < b
        elif op == "CLR.W": R[rn(args[0])] = 0; Z = 1
        elif op == "CLR.B": wr(int(args[0][2]), 0)
        elif op == "EXTU.B": R[rn(args[0])] &= 0xFF
        elif op in ("CMP.W", "CMP.B") and absa:          # CMP #imm,@aa:16 (DP = 4: Rosetta settings in RAM)
            assert page["DP"] == 4
            ad = int(absa.group(1), 16)
            a = (ram[ad] << 8 | ram[ad + 1]) if op == "CMP.W" else ram[ad]
            b = imm(args[0]); Z, C = a == b, a < b
        elif op == "CMP.W": a, b = R[rn(args[1])], imm(args[0]); Z, C = a == b, a < b
        elif op == "BTST.B": Z = not (R[rn(args[1])] >> int(args[0][1:])) & 1
        elif op == "AND.B": r = rn(args[1]); R[r] = (R[r] & 0xFF00) | (R[r] & imm(args[0]) & 0xFF); Z = (R[r] & 0xFF) == 0
        elif op == "ADD.W" and args[0].startswith("#"): R[rn(args[1])] = (R[rn(args[1])] + int(args[0][1:])) & 0xFFFF
        elif op == "ADD.W": R[rn(args[1])] = (R[rn(args[1])] + R[rn(args[0])]) & 0xFFFF
        elif op == "SUB.W": r = rn(args[1]); R[r] = (R[r] - imm(args[0])) & 0xFFFF; Z = R[r] == 0
        elif op == "SUB.B": r = rn(args[1]); v = ((R[r] & 0xFF) - imm(args[0])) & 0xFF; R[r] = (R[r] & 0xFF00) | v; Z = v == 0
        elif op in ("BEQ", "BNE", "BCC", "BRA"):
            take = {"BEQ": Z, "BNE": not Z, "BCC": not C, "BRA": True}[op]
            if take: nxt = i.target
        else: raise RuntimeError("emu: unhandled %05X %s" % (pc, t))
        pc = nxt


def build(frames_px, holds, tick, out, probe=False, rom=None, bootflag=False,
          hook=HOOK, hook_orig=HOOK_ORIG, player=PLAYER, limit=None, hook_tail=b"\x00\x00"):
    """rom = an already-patched image to add the boot animation to (default: the stock dump).
    hook / hook_orig / player / limit: boot-screen call site, its original bytes, where the player + frames go and
    the first byte they must not reach (default: the GP5 page-1 free space)."""
    HOOK, HOOK_ORIG, PLAYER = hook, hook_orig, player
    LIMIT = limit if limit is not None else globals()["LIMIT"]
    data_page = PLAYER >> 16
    if rom is None:
        rom = bytearray(open(ROM_IN, "rb").read())
        assert hashlib.md5(rom).hexdigest() == STOCK_MD5, "input is not the stock dump"
    rom = bytearray(rom)
    assert rom[HOOK:HOOK + 6] == HOOK_ORIG, "hook bytes changed?"
    assert all(b == 0xFF for b in rom[PLAYER:LIMIT])        # (GP5: 0x1F596-0x1F59F hold Rosetta's page-1 stub)
    limit = PROBE_P1 if probe else LIMIT
    packed = [pack(p) for p in frames_px]
    holds = [max(1, min(127, h)) for h in holds]
    code, labels, marks = assemble(PLAYER, 0, tick, probe, bootflag, data_page)          # size pass
    data_addr = PLAYER + len(code)
    code, labels, marks = assemble(PLAYER, data_addr & 0xFFFF, tick, probe, bootflag, data_page)
    stream = codec2.build_stream(packed, holds, data_addr)
    assert codec2.decode_stream(stream, data_addr) == list(zip(holds, packed))
    end = data_addr + len(stream)
    if end > limit:
        sys.exit("too big: %d bytes over (stream %d B, room %d B)" % (end - limit, len(stream), limit - data_addr))
    rom[PLAYER:PLAYER + len(code)] = code
    rom[data_addr:end] = stream
    rom[HOOK:HOOK + 6] = bytes([0x03, PLAYER >> 16, (PLAYER >> 8) & 0xFF, PLAYER & 0xFF]) + hook_tail  # PJSR + NOP NOP (or RTS: skip the rest)
    rom_a17 = None
    if probe:
        assert rom[PROBE_P0] == 0xFF and rom[PROBE_P1] == 0xFF
        rom[PROBE_P0] = ord("O"); rom[PROBE_P1] = ord("1")
        rom_a17 = bytearray(rom); rom_a17[PROBE_P1] = ord("3")
    # emulate with fast delays: patch tick + inner loop count to 1 in a scratch copy (pages 2-3 = the A17=1 copy)
    emu = bytearray(rom) + (rom_a17 if probe else bytearray(rom))
    for k in ("tick", "inner", "ptick", "pinner"):
        if k in marks: emu[marks[k] + 1:marks[k] + 3] = b"\x00\x01"
    eframes, R, page, ram, sr, lcd, stack = emulate(bytes(emu), PLAYER, labels)
    assert eframes == packed, "emulated player output != input frames"
    if probe:
        assert bytes(lcd["vram"][0x5F0:0x5F3]) == b"O13", "probe markers wrong: %r" % bytes(lcd["vram"][0x5F0:0x5F3])
    assert page["DP"] == 4 and ram[0x130:0x156] == b"\xfc" * 0x26 and not any(ram[:0x130]) \
        and not any(ram[0x156:0x6AE]), "exit RAM state wrong"
    assert sr == 0 and stack == [] and lcd["cmd"] == 0x42 and lcd["cur"] == 0 \
        and lcd["io"] == {0xFDD8: 0, 0xFDDA: 0}, "exit LCD/IRQ state wrong"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(rom)
    # x4 for an SST39SF040 in the 27C010 socket: A17/A18 don't care (the probe build differs only at its A17 marker)
    open(out[:-4] + "_SST39SF040_x4.bin", "wb").write(bytes(rom + (rom_a17 or rom)) * 2)
    lst = out[:-4] + "_player.lst"
    with open(lst, "w", encoding="utf-8") as f:
        d, pc = Dis(bytes(rom)), PLAYER
        while pc < PLAYER + len(code):
            i = d.dec(pc); f.write("%05X: %-14s %s\n" % (pc, rom[pc:pc + i.len].hex(" "), i.text)); pc += i.len
        i = d.dec(HOOK); f.write("\nhook %05X: %s  %s\n" % (HOOK, rom[HOOK:HOOK + 6].hex(" "), i.text))
    print("frames %d, stream %d B, player %d B @%05X, data @%05X-%05X, free left %d B"
          % (len(packed), len(stream), len(code), PLAYER, data_addr, end - 1, LIMIT - end))
    print("emulator: player reproduced all %d frames, exits cleanly" % len(eframes))
    print("wrote %s  md5 %s" % (out, hashlib.md5(rom).hexdigest()))
    return packed, holds


def preview(packed, holds, path):
    ims = []
    for f in packed:
        s = Screen(b""); s.ram[:GBYTES] = f
        tmp = path + ".tmp.png"; to_png(s.pixels(), tmp, 3); ims.append(Image.open(tmp).copy()); os.remove(tmp)
    ims[0].save(path, save_all=True, append_images=ims[1:], duration=[max(20, h * 50) for h in holds], loop=0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("frames")
    ap.add_argument("--hold", type=int, default=1, help="hold per frame in ticks (GIF durations override)")
    ap.add_argument("--tick", type=lambda x: int(x, 0), default=0x4B, help="stock delay units per tick (stock boot screen = 700); 0x4B ~ 50 ms, estimate")
    ap.add_argument("--ms-per-tick", type=float, default=50.0, help="GIF ms per tick")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--probe", action="store_true", help="after the animation show ROM-space probe markers (see probe_src)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "build", "hrgp5_anim.bin"))
    a = ap.parse_args()
    px, durs = load_frames(a.frames, a.invert)
    holds = [round(dd / a.ms_per_tick) if dd else a.hold for dd in durs]
    packed, holds = build(px, holds, a.tick, a.out, a.probe)
    preview(packed, holds, a.out[:-4] + "_preview.gif")
