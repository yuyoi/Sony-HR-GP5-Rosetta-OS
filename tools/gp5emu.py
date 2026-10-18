# HR-GP5 emulator: H8/500 (H8/520, maximum mode) CPU + SED1335 LCD + key matrix, enough to boot the real
# firmware, render the LCD and drive the UI with key presses.  DSP / MIDI / RTC are stubs.
#   python -I tools/gp5emu.py ROM [--keys "SYSTEM,B,EXIT"] [--png out/screen.png] [--secs 3]
#   x4 SST images are fine (the first 128 KB is used).
import sys, os, struct
sys.path.insert(0, os.path.dirname(__file__))

KEYS = {"METER": 0, "TUNE": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "EDIT": 7, "PAGE": 7,
        "EXIT": 8, "SYSTEM": 9, "SAVE": 10, "BYPASS": 11}


def s8(x): return x - 0x100 if x & 0x80 else x
def s16(x): return x - 0x10000 if x & 0x8000 else x


class Halt(Exception): pass


class LCD:
    """SED1335 subset: CSRW, CSRDIR, MWRITE, MREAD, CGRAM ADR, SCROLL; 64 KB VRAM."""
    def __init__(s):
        s.vram = bytearray(0x10000); s.cmd = None; s.args = []; s.cur = 0; s.dir = 1

    def write(s, a0, v):
        if a0:                                   # command
            s.cmd, s.args = v, []
            if v in (0x4C, 0x4D, 0x4E, 0x4F): s.dir = {0x4C: 1, 0x4D: -1, 0x4E: -38, 0x4F: 38}[v]
            return
        if s.cmd == 0x46:
            s.args.append(v)
            if len(s.args) == 2: s.cur = s.args[0] | s.args[1] << 8
        elif s.cmd == 0x42:
            s.vram[s.cur & 0xFFFF] = v; s.cur = (s.cur + s.dir) & 0xFFFF
        else:
            s.args.append(v)

    def read(s, a0):
        if not a0: return 0x00                   # status: never busy
        v = s.vram[s.cur & 0xFFFF]; s.cur = (s.cur + s.dir) & 0xFFFF
        return v


class Emu:
    def __init__(s, rom):
        assert len(rom) >= 0x20000
        s.rom = bytes(rom[:0x20000])
        s.font = s.rom                          # text-layer glyph source (the GP5 small font at 0xA7C2)
        s.ram = bytearray(0x8000)               # page 4 SRAM
        s.onchip = bytearray(0x300)             # 0xFD00-0xFFFF (I/O area shadow + on-chip RAM + regs)
        s.R = [0] * 8
        s.pc = 0; s.cp = 0; s.dp = 0; s.ep = 0; s.tp = 0; s.br = 0; s.sr = 0x0700
        s.lcd = LCD()
        s.keys = set()                          # held key numbers
        s.fd01 = 0xFF
        s.icount = 0
        s.next_irq0 = 2000; s.next_tmr = 5000; s.next_tick = 3000
        s.rocker = 0xF                          # rocker position code on FD02 row 0 high nibble (0xF = centre)
        s.midi_in = []; s.next_midi = 0         # bytes for MIDI IN (SCI at FFD8, RXI vector 0xD4), one per 480 steps (31250 baud)
        s.trace = None
        s.adc = {0xFFE0: 0xC0, 0xFFE2: 0x00}
        s.fastloops = True
        s.strict = [(0x0B3C8, 0x0F7D8), (0x1F596, 0x20000)]   # Rosetta code + boot player: odd word access = bug
        s.odd_count = 0
        s.ram_ok = {0x62E0} | set(range(0x1280, 0x13E0))   # Sony vars Rosetta may write: 0x462E0 "not in PLAY", 0x41280 DSP-current program (LFO),
                                                            # 0x41330 DSP-side copy (DDS-DSP SAMPLER unload)
        s.ramw = None                           # set of page-4 offsets written (RAM usage survey)
        s.dsplog = None                         # list of (port, value, icount) for FD00/FD10/FD11 writes
        s.frc_base = {}
        v = struct.unpack(">I", s.rom[0:4])[0]
        s.cp, s.pc = (v >> 16) & 0xFF, v & 0xFFFF

    # ---------------- bus ----------------
    def rd8(s, a):
        pg, o = (a >> 16) & 0xFF, a & 0xFFFF
        if pg == 0:
            if o < 0xFD00: return s.rom[o]
            if o < 0xFD80: return s.io_rd(o)
            if o in s.adc: return s.adc[o]
            if o in (0xFFC4, 0xFFDC): return s.onchip[o - 0xFD00] | 0x84   # SCI SSR: TDRE + TEND (MIDI out ready)
            if o in (0xFF92, 0xFFA2, 0xFFB2):          # free-running timer counters (hi byte)
                return ((s.icount * 2 - s.frc_base.get(o, 0)) >> 8) & 0xFF
            if o in (0xFF93, 0xFFA3, 0xFFB3):
                return (s.icount * 2 - s.frc_base.get(o - 1, 0)) & 0xFF
            return s.onchip[o - 0xFD00]
        if pg == 1: return s.rom[0x10000 | o]
        if pg == 4 and o < 0x8000: return s.ram[o]
        return 0xFF

    def wr8(s, a, v):
        v &= 0xFF
        pg, o = (a >> 16) & 0xFF, a & 0xFFFF
        if pg == 0:
            if o < 0xFD00: return                 # ROM
            if o < 0xFD80: return s.io_wr(o, v)
            s.onchip[o - 0xFD00] = v
            if s.ramw is not None and 0xFD80 <= o < 0xFF80: s.ramw.add(0x10000 | o)
            if o in (0xFF92, 0xFFA2, 0xFFB2): s.frc_base[o] = s.icount * 2   # counter write = restart (any value)
            return
        if pg == 4 and o < 0x8000:
            if s.strict and not (o < 0x06AE or 0x7000 <= o < 0x7900 or o >= 0x7E00 or o in s.ram_ok):
                pc = (s.cp << 16) | s.pc
                if any(lo <= pc < hi for lo, hi in s.strict):
                    raise Halt("Rosetta code wrote RAM %05X (pc %06X): outside shadow / settings / stack" % (0x40000 | o, pc))
            s.ram[o] = v
            if s.ramw is not None: s.ramw.add(o)

    def odd(s, a, what):
        """odd word access: Sony's own code does this (8-bit bus tolerates it); our code must never"""
        pc = (s.cp << 16) | s.pc
        if any(lo <= pc < hi for lo, hi in s.strict):
            raise Halt("address error: word %s at %06X (pc %06X)" % (what, a, pc))
        s.odd_count += 1

    def rd16(s, a):
        if a & 1: s.odd(a, "read")
        return (s.rd8(a) << 8) | s.rd8((a & 0xFF0000) | ((a + 1) & 0xFFFF))

    def wr16(s, a, v):
        if a & 1: s.odd(a, "write")
        s.wr8(a, v >> 8); s.wr8((a & 0xFF0000) | ((a + 1) & 0xFFFF), v)

    def io_rd(s, o):
        if o == 0xFD02:
            row = [r for r in range(3) if not (s.fd01 >> r) & 1]
            v = 0xFF
            if row:
                r = row[0]
                for c in range(4):
                    if r * 4 + c in s.keys: v &= ~(1 << c)
                if r == 0: v = (v & 0x0F) | ((s.rocker & 0xF) << 4)   # the rocker: position code, 0xF = centre
            return v
        if o == 0xFD10: return 0xFF              # DSP ready
        if o == 0xFD03: return 0xFD              # DSP: bit0 ready, bit1 idle, bit4 ready
        if o == 0xFD12: return 0x00
        if o == 0xFD30: return s.lcd.read(0)
        if o == 0xFD31: return s.lcd.read(1)
        return s.onchip[o - 0xFD00]

    def io_wr(s, o, v):
        if s.dsplog is not None and o in (0xFD00, 0xFD10, 0xFD11):
            s.dsplog.append((o, v, s.icount))
        s.onchip[o - 0xFD00] = v
        if o == 0xFD01: s.fd01 = v
        elif o == 0xFD30: s.lcd.write(0, v)
        elif o == 0xFD31: s.lcd.write(1, v)

    # ---------------- helpers ----------------
    def fetch8(s):
        v = s.rd8((s.cp << 16) | s.pc); s.pc = (s.pc + 1) & 0xFFFF; return v

    def fetch16(s):
        v = (s.fetch8() << 8); return v | s.fetch8()

    def page_of(s, n): return s.dp if n < 4 else s.ep if n < 6 else s.tp

    def push16(s, v):
        s.R[7] = (s.R[7] - 2) & 0xFFFF; s.wr16((s.tp << 16) | s.R[7], v)

    def pop16(s):
        v = s.rd16((s.tp << 16) | s.R[7]); s.R[7] = (s.R[7] + 2) & 0xFFFF; return v

    def setnz(s, v, w):
        m = 0xFFFF if w else 0xFF
        v &= m
        s.sr = (s.sr & ~0x0C) | (0x08 if v & (0x8000 if w else 0x80) else 0) | (0x04 if v == 0 else 0)

    def flags(s, n=None, z=None, v=None, c=None):
        sr = s.sr
        if n is not None: sr = (sr & ~8) | (8 if n else 0)
        if z is not None: sr = (sr & ~4) | (4 if z else 0)
        if v is not None: sr = (sr & ~2) | (2 if v else 0)
        if c is not None: sr = (sr & ~1) | (1 if c else 0)
        s.sr = sr

    N = property(lambda s: (s.sr >> 3) & 1); Z = property(lambda s: (s.sr >> 2) & 1)
    V = property(lambda s: (s.sr >> 1) & 1); C = property(lambda s: s.sr & 1)

    def creg_get(s, n, w):
        if n == 0: return s.sr if w else s.sr & 0xFF
        if n == 1: return s.sr & 0xFF
        return {3: s.br, 4: s.ep, 5: s.dp, 7: s.tp}.get(n, 0)

    def creg_set(s, n, v, w):
        if n == 0 and w: s.sr = v & 0x870F if False else v & 0xFFFF
        elif n in (0, 1): s.sr = (s.sr & 0xFF00) | (v & 0xFF)
        elif n == 3: s.br = v & 0xFF
        elif n == 4: s.ep = v & 0xFF
        elif n == 5: s.dp = v & 0xFF
        elif n == 7: s.tp = v & 0xFF

    # operand = ("r", n) or ("m", addr24)
    def ea(s, ea, w):
        if 0xA0 <= ea <= 0xAF: return ("r", ea & 7)
        size = 2 if w else 1
        n = ea & 7
        if ea >= 0xF0: d = s16(s.fetch16()); return ("m", (s.page_of(n) << 16) | ((s.R[n] + d) & 0xFFFF))
        if ea >= 0xE0: d = s8(s.fetch8()); return ("m", (s.page_of(n) << 16) | ((s.R[n] + d) & 0xFFFF))
        if ea >= 0xD0: return ("m", (s.page_of(n) << 16) | s.R[n])
        if ea >= 0xC0:
            a = s.R[n]; s.R[n] = (a + (2 if (w or n == 7) else 1)) & 0xFFFF
            return ("m", (s.page_of(n) << 16) | a)
        if ea >= 0xB0:
            s.R[n] = (s.R[n] - (2 if (w or n == 7) else 1)) & 0xFFFF
            return ("m", (s.page_of(n) << 16) | s.R[n])
        if (ea & 0xE7) == 0x05:
            if ea & 0x10: return ("m", (s.dp << 16) | s.fetch16())
            return ("m", (s.br << 8) | s.fetch8())
        raise Halt("bad ea %02X" % ea)

    def get(s, opd, w):
        k, x = opd
        if k == "r": return s.R[x] if w else s.R[x] & 0xFF
        return s.rd16(x) if w else s.rd8(x)

    def put(s, opd, v, w):
        k, x = opd
        if k == "r":
            s.R[x] = v & 0xFFFF if w else (s.R[x] & 0xFF00) | (v & 0xFF)
        elif w: s.wr16(x, v & 0xFFFF)
        else: s.wr8(x, v & 0xFF)

    def regget(s, n, w): return s.R[n] if w else s.R[n] & 0xFF
    def regput(s, n, v, w): s.R[n] = v & 0xFFFF if w else (s.R[n] & 0xFF00) | (v & 0xFF)

    # arithmetic with flags
    def add(s, a, b, w, cin=0, keepz=False):
        m, sb = (0xFFFF, 0x8000) if w else (0xFF, 0x80)
        r = a + b + cin
        res = r & m
        v = ((a ^ res) & (b ^ res) & sb) != 0
        z = (res == 0) if not keepz else (s.Z and res == 0)
        s.flags(n=res & sb, z=z, v=v, c=r > m)
        return res

    def sub(s, a, b, w, cin=0, keepz=False):
        m, sb = (0xFFFF, 0x8000) if w else (0xFF, 0x80)
        r = a - b - cin
        res = r & m
        v = ((a ^ b) & (a ^ res) & sb) != 0
        z = (res == 0) if not keepz else (s.Z and res == 0)
        s.flags(n=res & sb, z=z, v=v, c=r < 0)
        return res

    def cond(s, c):
        N, Z, V, C = s.N, s.Z, s.V, s.C
        return [True, False, not (C or Z), bool(C or Z), not C, bool(C), not Z, bool(Z),
                not V, bool(V), not N, bool(N), not (N ^ V), bool(N ^ V), not (Z or (N ^ V)), bool(Z or (N ^ V))][c]

    # ---------------- exceptions ----------------
    def exception(s, vec, level=None):
        s.push16(s.pc); s.push16(s.cp); s.push16(s.sr)
        if level is not None: s.sr = (s.sr & ~0x0700) | (level << 8)
        v = struct.unpack(">I", s.rom[vec:vec + 4])[0]
        s.cp, s.pc = (v >> 16) & 0xFF, v & 0xFFFF

    def irqs(s):
        mask = (s.sr >> 8) & 7
        if s.icount >= s.next_irq0:
            s.next_irq0 = s.icount + 1500
            if mask < 6: s.exception(0x80, 6); return
        if s.icount >= s.next_tick:                # 8-bit timer CMIA: 5 ms system tick (key scan, DSP, pedal)
            s.next_tick = s.icount + 6000
            if mask < 6: s.exception(0xC0, 6); return
        if s.icount >= s.next_tmr:
            s.next_tmr = s.icount + 4000
            if mask < 7: s.exception(0xB4, 7); return
        if s.midi_in and s.icount >= s.next_midi and mask < 6:
            s.next_midi = s.icount + 480
            s.onchip[0xFFDD - 0xFD00] = s.midi_in.pop(0)
            s.onchip[0xFFDC - 0xFD00] |= 0x40   # RDRF
            s.exception(0xD4, 6); return

    # ---------------- execution ----------------
    def step(s):
        s.irqs()
        pc0 = (s.cp << 16) | s.pc
        if s.trace is not None: s.trace(s, pc0)
        op = s.fetch8()
        s.icount += 1
        if op >= 0xA0 or (op & 0xE7) == 0x05: return s.general(op)
        if op >= 0x60:                           # MOV:L / MOV:S (@aa:8), MOV:F (@(d8,FP))
            w = (op >> 3) & 1
            d = s.fetch8()
            if op >= 0x80: opd = ("m", (s.tp << 16) | ((s.R[6] + s8(d)) & 0xFFFF))
            else: opd = ("m", (s.br << 8) | d)
            n = op & 7
            if op & 0x10: v = s.regget(n, w); s.put(opd, v, w)
            else: v = s.get(opd, w); s.regput(n, v, w)
            s.setnz(v, w); s.flags(v=0); return
        if op >= 0x40:                           # CMP:E/I, MOV:E/I
            w = (op >> 3) & 1
            imm = s.fetch16() if w else s.fetch8()
            n = op & 7
            if op & 0x10: s.regput(n, imm, w); s.setnz(imm, w); s.flags(v=0)
            else: s.sub(s.regget(n, w), imm, w)
            return
        if op >= 0x20:
            d = s16(s.fetch16()) if op & 0x10 else s8(s.fetch8())
            if s.cond(op & 15): s.pc = (s.pc + d) & 0xFFFF
            return
        return s.misc(op)

    def misc(s, op):
        if op == 0x00: return
        if op in (0x03, 0x13):
            pg = s.fetch8(); a = s.fetch16()
            if op == 0x03: s.push16(s.pc); s.push16(s.cp)
            s.cp, s.pc = pg, a; return
        if op in (0x01, 0x06, 0x07, 0x11):
            o2 = s.fetch8()
            if op == 0x11 and o2 >= 0xD0:
                n = o2 & 7
                if o2 >= 0xF0: t = s.R[n] + s16(s.fetch16())
                elif o2 >= 0xE0: t = s.R[n] + s8(s.fetch8())
                else: t = s.R[n]
                if o2 & 8: s.push16(s.pc)
                s.pc = t & 0xFFFF; return
            if (o2 & 0xF8) == 0xB8:
                n = o2 & 7; d = s8(s.fetch8())
                ccok = {0x01: False, 0x06: not s.Z, 0x07: bool(s.Z)}.get(op, False)
                if ccok: return
                s.R[n] = (s.R[n] - 1) & 0xFFFF
                if s.R[n] != 0xFFFF: s.pc = (s.pc + d) & 0xFFFF
                return
            if op == 0x11 and o2 in (0x14, 0x1C):
                imm = s.fetch16() if o2 == 0x1C else s.fetch8()
                s.cp = s.pop16() & 0xFF; s.pc = s.pop16(); s.R[7] = (s.R[7] + imm) & 0xFFFF; return
            if op == 0x11 and o2 == 0x19:
                s.cp = s.pop16() & 0xFF; s.pc = s.pop16(); return
            raise Halt("misc %02X %02X" % (op, o2))
        if op == 0x02:                           # LDM @SP+,(list)  R0 first
            lst = s.fetch8()
            for n in range(8):
                if lst >> n & 1: s.R[n] = s.pop16()
            return
        if op == 0x12:                           # STM (list),@-SP  R7 first
            lst = s.fetch8()
            for n in range(7, -1, -1):
                if lst >> n & 1: s.push16(s.R[n])
            return
        if op in (0x04, 0x0C): return s.immop(op == 0x0C)
        if op == 0x08:
            v = s.fetch8(); s.exception(0x40 + (v & 15) * 4); return
        if op == 0x09:
            if s.V: s.exception(0x10)
            return
        if op == 0x0A:
            s.sr = s.pop16(); s.cp = s.pop16() & 0xFF; s.pc = s.pop16(); return
        if op in (0x0E, 0x1E):
            d = s16(s.fetch16()) if op == 0x1E else s8(s.fetch8())
            s.push16(s.pc); s.pc = (s.pc + d) & 0xFFFF; return
        if op == 0x0F:
            s.R[7] = s.R[6]; s.R[6] = s.pop16(); return
        if op in (0x10, 0x18):
            a = s.fetch16()
            if op == 0x18: s.push16(s.pc)
            s.pc = a; return
        if op in (0x14, 0x1C):
            imm = s.fetch16() if op == 0x1C else s.fetch8()
            s.pc = s.pop16(); s.R[7] = (s.R[7] + imm) & 0xFFFF; return
        if op in (0x17, 0x1F):
            imm = s.fetch16() if op == 0x1F else s.fetch8()
            s.push16(s.R[6]); s.R[6] = s.R[7]; s.R[7] = (s.R[7] - imm) & 0xFFFF; return
        if op == 0x19: s.pc = s.pop16(); return
        if op == 0x1A: return                    # SLEEP: let the next IRQ wake us
        raise Halt("op %02X at %X" % (op, s.pc - 1))

    def immop(s, w):
        imm = s.fetch16() if w else s.fetch8()
        op = s.fetch8()
        n = op & 7
        g = op >> 3
        if (op & 0xC8) == 0x48 and op < 0x70:    # ORC / ANDC / XORC
            cur = s.creg_get(n, w)
            v = {0x48: cur | imm, 0x58: cur & imm, 0x68: cur ^ imm}[op & 0xF8]
            s.creg_set(n, v, w); return
        return s.alu(g, n, ("i", imm), w, creg=(0x88 <= op < 0x90))

    def alu(s, g, n, src, w, creg=False, dst_ea=None):
        """g = GEN index; src operand value provider; destination register n (or creg)."""
        sv = src[1] if src[0] == "i" else s.get(src, w)
        if g == 0x11 or creg:                     # LDC
            s.creg_set(n, sv, w); return
        d = s.regget(n, w)
        if g == 4: s.regput(n, s.add(d, sv, w), w)
        elif g == 5:                              # ADDS (no flags, sign-extended to word)
            s.R[n] = (s.R[n] + (s16(sv) if w else s8(sv))) & 0xFFFF
        elif g == 6: s.regput(n, s.sub(d, sv, w), w)
        elif g == 7: s.R[n] = (s.R[n] - (s16(sv) if w else s8(sv))) & 0xFFFF
        elif g == 8: r = d | sv; s.regput(n, r, w); s.setnz(r, w); s.flags(v=0)
        elif g == 10: r = d & sv; s.regput(n, r, w); s.setnz(r, w); s.flags(v=0)
        elif g == 12: r = d ^ sv; s.regput(n, r, w); s.setnz(r, w); s.flags(v=0)
        elif g == 14: s.sub(d, sv, w)
        elif g == 16: s.regput(n, sv, w); s.setnz(sv, w); s.flags(v=0)
        elif g == 20: s.regput(n, s.add(d, sv, w, s.C, keepz=True), w)
        elif g == 22: s.regput(n, s.sub(d, sv, w, s.C, keepz=True), w)
        elif g == 21:                              # MULXU
            if w:
                r = d * sv; s.R[n] = (r >> 16) & 0xFFFF; s.R[(n + 1) & 7] = r & 0xFFFF
                s.flags(n=r & 0x80000000, z=r == 0, v=0, c=0)
            else:
                r = (d & 0xFF) * (sv & 0xFF); s.R[n] = r; s.flags(n=r & 0x8000, z=r == 0, v=0, c=0)
        elif g == 23:                              # DIVXU
            if sv == 0: s.flags(v=1, c=0); return
            if w:
                dd = (s.R[n] << 16) | s.R[(n + 1) & 7]
                q, r = divmod(dd, sv)
                if q > 0xFFFF: s.flags(v=1); return
                s.R[n] = r; s.R[(n + 1) & 7] = q; s.flags(n=q & 0x8000, z=q == 0, v=0, c=0)
            else:
                q, r = divmod(s.R[n], sv & 0xFF)
                if q > 0xFF: s.flags(v=1); return
                s.R[n] = (r << 8) | q; s.flags(n=q & 0x80, z=q == 0, v=0, c=0)
        else: raise Halt("alu g=%d" % g)

    def general(s, eab):
        w = (eab >> 3) & 1
        # peek op byte after the ea bytes without consuming: compute ea length first
        if eab >= 0xF0 or (eab & 0xF7) == 0x15 and eab & 0x10: elen = 2
        elif eab >= 0xE0 or (eab & 0xE7) == 0x05: elen = 1 if not (eab & 0x10 and eab < 0xA0) else 2
        else: elen = 0
        if (eab & 0xE7) == 0x05: elen = 2 if eab & 0x10 else 1
        elif eab >= 0xF0: elen = 2
        elif eab >= 0xE0: elen = 1
        else: elen = 0
        op = s.rd8((s.cp << 16) | ((s.pc + elen) & 0xFFFF))
        if (eab & 0xF8) == 0xA8 and (op & 0xF8) == 0x90:          # XCH
            s.fetch8(); a, b = eab & 7, op & 7
            s.R[a], s.R[b] = s.R[b], s.R[a]; return
        opd = s.ea(eab, w)
        op = s.fetch8()
        g = op >> 3
        GEN = {4, 5, 6, 7, 8, 10, 12, 14, 16, 17, 18, 19, 20, 21, 22, 23}
        if g in GEN:
            n = op & 7
            if g == 18:                            # MOV Rs -> ea
                v = s.regget(n, w); s.put(opd, v, w); s.setnz(v, w); s.flags(v=0); return
            if g == 19:                            # STC creg -> ea
                s.put(opd, s.creg_get(n, w), w); return
            return s.alu(g, n, opd, w, creg=(g == 17))
        if op & 0x40:                              # bit ops
            bitn = (op & 15) if op & 0x80 else s.R[op & 7]
            bitn &= 15 if w else 7
            v = s.get(opd, w)
            s.flags(z=not (v >> bitn) & 1)
            kind = (op >> 4) & 3
            if kind == 0: s.put(opd, v | (1 << bitn), w)
            elif kind == 1: s.put(opd, v & ~(1 << bitn), w)
            elif kind == 2: s.put(opd, v ^ (1 << bitn), w)
            return
        if (op & 0xF0) == 0x10:                    # unary
            return s.unary(op & 15, opd, w)
        if (op & 0xFC) == 0x04:                    # CMP:G / MOV:G #imm
            imm = s.fetch16() if op & 1 else s.fetch8()
            if not (op & 1) and w: imm = s16(s8(imm)) & 0xFFFF
            if op & 2: s.put(opd, imm, w); s.setnz(imm, w); s.flags(v=0)
            else: s.sub(s.get(opd, w), imm, w)
            return
        if (op & 0xFA) == 0x08:                    # ADD:Q
            q = (2 if op & 1 else 1) * (-1 if op & 4 else 1)
            v = s.get(opd, w)
            r = s.add(v, q & (0xFFFF if w else 0xFF), w) if q > 0 else s.sub(v, -q, w)
            s.put(opd, r, w); return
        if op == 0x00:
            o2 = s.fetch8()
            if (o2 & 0xE8) == 0xA0:                # DADD / DSUB (byte, BCD)
                a, b = s.R[eab & 7] & 0xFF, s.R[o2 & 7] & 0xFF
                def bcd(x): return (x >> 4) * 10 + (x & 15)
                if o2 & 0x10: r = bcd(b) - bcd(a) - s.C; c = r < 0; r %= 100
                else: r = bcd(a) + bcd(b) + s.C; c = r > 99; r %= 100
                rv = ((r // 10) << 4) | (r % 10)
                s.regput(o2 & 7, rv, 0); s.flags(z=(s.Z and rv == 0), c=c); return
            raise Halt("MOVTPE/MOVFPE not supported")
        raise Halt("general %02X %02X" % (eab, op))

    def unary(s, k, opd, w):
        m, sb = (0xFFFF, 0x8000) if w else (0xFF, 0x80)
        v = s.get(opd, w)
        if k == 0:                                 # SWAP (word register)
            n = opd[1]; s.R[n] = ((s.R[n] << 8) | (s.R[n] >> 8)) & 0xFFFF; s.setnz(s.R[n], 1); return
        if k == 1: n = opd[1]; s.R[n] = s8(s.R[n] & 0xFF) & 0xFFFF; s.setnz(s.R[n], 1); s.flags(v=0, c=0); return
        if k == 2: n = opd[1]; s.R[n] &= 0xFF; s.setnz(s.R[n], 1); s.flags(v=0, c=0); return
        if k == 3: s.put(opd, 0, w); s.flags(n=0, z=1, v=0, c=0); return
        if k == 4: r = s.sub(0, v, w); s.put(opd, r, w); return
        if k == 5: r = (~v) & m; s.put(opd, r, w); s.setnz(r, w); s.flags(v=0, c=0); return
        if k == 6: s.setnz(v, w); s.flags(v=0, c=0); return
        if k == 7: s.setnz(v, w); s.flags(v=0, c=0); s.put(opd, v | sb, w); return
        c = s.C
        if k in (8, 10):                           # SHAL / SHLL
            r = (v << 1) & m; s.put(opd, r, w); s.setnz(r, w)
            s.flags(c=v & sb, v=(k == 8) and ((v ^ r) & sb) != 0 or 0); return
        if k == 9: r = (v >> 1) | (v & sb); s.put(opd, r, w); s.setnz(r, w); s.flags(c=v & 1, v=0); return
        if k == 11: r = v >> 1; s.put(opd, r, w); s.setnz(r, w); s.flags(c=v & 1, v=0); return
        if k == 12: r = ((v << 1) | (1 if v & sb else 0)) & m; s.put(opd, r, w); s.setnz(r, w); s.flags(c=v & sb, v=0); return
        if k == 13: r = (v >> 1) | (sb if v & 1 else 0); s.put(opd, r, w); s.setnz(r, w); s.flags(c=v & 1, v=0); return
        if k == 14: r = ((v << 1) | c) & m; s.put(opd, r, w); s.setnz(r, w); s.flags(c=v & sb, v=0); return
        if k == 15: r = (v >> 1) | (sb if c else 0); s.put(opd, r, w); s.setnz(r, w); s.flags(c=v & 1, v=0); return

    # ---------------- run helpers ----------------
    def run(s, n):
        for _ in range(n):
            if s.fastloops: s.fast()
            s.step()

    def fast(s):
        """collapse tight 'SUB.W #1,Rn ; BNE back' delay loops"""
        a = (s.cp << 16) | s.pc
        if a == 0x019D6 and s.R[0] > 1: s.R[0] = 1        # delay_short (NOP-padded loop)
        if s.rd8(a) == 0x0C and s.rd8(a + 1) == 0x00 and s.rd8(a + 2) == 0x01 and (s.rd8(a + 3) & 0xF8) == 0x30 \
                and s.rd8(a + 4) == 0x26 and s.rd8(a + 5) in (0xFA, 0xF8, 0xF6):
            back = s8(s.rd8(a + 5))
            if back == -6:                         # loop is just these two instructions
                n = s.rd8(a + 3) & 7
                if s.R[n] > 1: s.R[n] = 1

    def press(s, key, hold_steps=60000, gap_steps=150000):
        k = KEYS[key.upper()] if isinstance(key, str) else key
        s.keys.add(k); s.run(hold_steps); s.keys.discard(k); s.run(gap_steps)

    # rocker position codes (Sony's table at 0x134E): +1 = 0xB, -1 = 0xE, +5 = 0xD, -5 = 0x8, +10 = 0x1, -10 = 0xC
    ROCK = {1: 0xB, -1: 0xE, 2: 0x9, -2: 0xA, 5: 0xD, -5: 0x8, 10: 0x1, -10: 0xC}

    def rock(s, step, hold_steps=60000, gap_steps=150000):
        s.rocker = s.ROCK[step]; s.run(hold_steps); s.rocker = 0xF; s.run(gap_steps)

    def dial(s, delta, steps=40000):      # the encoder (FDE9)
        s.onchip[0xFDE9 - 0xFD00] = delta & 0xFF; s.run(steps)

    # ---------------- LCD render ----------------
    def pixels(s):
        W, H, CR = 228, 40, 38
        v = s.lcd.vram
        px = [[0] * W for _ in range(H)]
        for y in range(H):
            for b in range(CR):
                bb = v[y * CR + b]
                for x in range(6): px[y][b * 6 + x] = (bb >> (7 - x)) & 1
        for row in range(5):
            for col in range(CR):
                c = v[0x5F0 + row * CR + col]
                if c == 0: continue
                if 0x80 <= c < 0xA0: g = v[0x0700 + (c - 0x80) * 8:][:8]
                else: g = s.font[0xA7C2 + (c & 0x7F) * 7:][:7]
                for r, gv in enumerate(g):
                    for x in range(6): px[row * 8 + r][col * 6 + x] ^= (gv >> (7 - x)) & 1
        return px

    def png(s, path, scale=4):
        from gp5lcd import to_png
        to_png(s.pixels(), path, scale)

    def text(s):
        v = s.lcd.vram
        return "\n".join("".join(chr(c) if 32 <= c < 127 else "." for c in v[0x5F0 + r * 38:0x5F0 + r * 38 + 38]) for r in range(5))


def boot(rom, secs=3.0, steps_per_sec=1_500_000):
    e = Emu(rom)
    e.run(int(secs * steps_per_sec))
    return e


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--secs", type=float, default=3.0, help="boot time to emulate before keys")
    ap.add_argument("--keys", default="", help="comma list, e.g. SYSTEM,A,EXIT; dial+N / dial-N for the dial")
    ap.add_argument("--png", default=None)
    a = ap.parse_args()
    e = boot(open(a.rom, "rb").read(), a.secs)
    for k in [k for k in a.keys.split(",") if k]:
        if k.lower().startswith("dial"): e.dial(int(k[4:]))
        else: e.press(k)
    print(e.text())
    print("pc %02X:%04X  icount %d" % (e.cp, e.pc, e.icount))
    if a.png: e.png(a.png); print("wrote", a.png)
