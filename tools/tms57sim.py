# license:BSD-3-Clause
# copyright-holders:Olivier Galibert (MAME tms57002.cpp / tmsinstr.lst semantics); Python port: JSW and Claude
# TMS57002 "DASP" simulator + assembler (same text syntax as tools/tms57dis.py, round-trips through it).
#
# Semantics follow MAME (ref/mame_tms): 24-bit data words (dmem), 32-bit coefficients (cmem), 32-bit ALU (aacc),
# 48-bit MAC (macc, results visible to %mo / %mv two instructions later), circular dmem (ba0--, ba1++ per sample unless
# ST0.INCS), external delay memory with one access in flight, one nibble / byte per instruction (xba-- per sample).
# Instructions MAME leaves empty but Sony's program uses: zacc (aacc = 0), dos (so = dmem word). Others raise.
#   python -I tools/tms57sim.py --selftest
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tms57dis

M32, M24 = 0xFFFFFFFF, 0xFFFFFF
ST0_INCS, ST0_WORD, ST0_SEL, ST0_M = 0x000001, 0x004000, 0x008000, 0x030000
ST1_AOV, ST1_SFAI, ST1_SFAO, ST1_AOVM, ST1_MOVM, ST1_MOV = 0x1, 0x2, 0x4, 0x8, 0x20, 0x40
ST1_SFMA_SH, ST1_SFMO_SH, ST1_RND_SH, ST1_CRM_SH = 7, 11, 15, 18
ST1_DBP = 0x100000
POST = {0x0c, 0x1c, 0x1d, 0x1e, 0x1f, 0x3a, 0x3c, 0x3d, 0x40, 0x41, 0x44, 0x45} | set(range(0x48, 0x4c)) | \
       {0x50, 0x51, 0x54, 0x55} | set(range(0x58, 0x5c)) | set(range(0x60, 0x64)) | set(range(0x68, 0x70))
ROUNDING = [0, 1 << 15, 1 << 23, 1 << 17, 1 << 31]
A64 = (1 << 64) - 1
RMASK = [A64, A64 - (1 << 16) + 1, A64 - (1 << 24) + 1, A64 - (1 << 18) + 1, A64 - (1 << 32) + 1]


def s32(x): x &= M32; return x - (1 << 32) if x & 0x80000000 else x
def s64(x): x &= A64; return x - (1 << 64) if x >> 63 else x


class TMS:
    def __init__(s, mem_nibbles=1 << 20):
        s.prog = [0] * 256
        s.cmem = [0] * 256; s.dmem0 = [0] * 256; s.dmem1 = [0] * 32
        s.st0 = s.st1 = 0
        s.aacc = 0; s.macc = s.macc_read = s.macc_write = 0; s.creg = 0
        s.cat1_lag = 2; s.macc_hist = [0, 0, 0]; s.macc_h4 = 0   # cat1 macc readers ('add c,m'): stage t - cat1_lag
        s.pc = s.ca = s.id = s.ba0 = s.ba1 = 0
        s.xba = s.xoa = s.xwr = s.xrd = s.txrd = s.xm_adr = 0
        s.reading = s.writing = False
        s.xm = bytearray(mem_nibbles)
        s.si = [0] * 4; s.so = [0] * 4
        s.upd = []; s.sa = 0; s.updating = False
        s.host = None
        s.rptc = s.rptc_next = 0
        s.icount = 0

    # ---- host side ----
    def load(s, st0, st1, words, cmem=None):
        s.st0, s.st1 = st0, st1
        s.prog = list(words) + [0] * (256 - len(words))
        if cmem:
            for a, v in (cmem.items() if isinstance(cmem, dict) else enumerate(cmem)): s.cmem[a] = v & M32

    def host_update(s, addr, value):           # CLOAD: address byte + 4 data bytes (MAME queue semantics)
        assert len(s.upd) < 16, "update queue full"
        s.sa = addr & 0xFF; s.upd.append(value & M32)

    # ---- operand access ----
    def crm(s):
        if s.upd: return 0
        c = (s.st1 >> ST1_CRM_SH) & 3
        return c if c <= 2 else 0

    def get_cmem(s, a):
        if s.sa == a and s.upd: s.updating = True
        if s.updating:
            s.cmem[a] = s.upd.pop(0)
            if not s.upd: s.updating = False
            return s.cmem[a]
        v = s.cmem[a]; c = s.crm()
        if c == 1: return v & 0xFFFF0000
        if c == 2: return (v << 16) & M32
        return v

    def dadr(s, w, primary_d, inc):
        if primary_d and (w & 0x100): idx = w & 0xFF
        else: idx = s.id
        if s.st1 & ST1_DBP: return 1, (idx + s.ba1) & 0x1F
        return 0, (idx + s.ba0) & 0xFF

    def mo(s):
        sfmo = (s.st1 >> ST1_SFMO_SH) & 3; rnd = (s.st1 >> ST1_RND_SH) & 7
        if rnd > 4: rnd = 0
        m = s.macc_read; over = False
        pre_mask = [0xf800000000000, 0xfe00000000000, 0xff80000000000, None][sfmo]
        if pre_mask is not None:
            m1 = m & pre_mask
            if m1 and m1 != pre_mask: over = True
        if sfmo == 1: m <<= 2
        elif sfmo == 2: m <<= 4
        elif sfmo == 3: m >>= 8
        m = s64((m + ROUNDING[rnd]) & RMASK[rnd])
        m1 = m & 0xf800000000000
        if m1 and m1 != 0xf800000000000: over = True
        if over:
            s.st1 |= ST1_MOV
            if s.st1 & ST1_MOVM: m = s64(0xffff800000000000) if m & 0x8000000000000 else 0x00007fffffffffff
        return m

    def mv(s):
        sfmo = (s.st1 >> ST1_SFMO_SH) & 3
        m = s.macc_read
        mask = [0xf800000000000, 0xfe00000000000, 0xff80000000000, None][sfmo]
        if mask is not None:
            m1 = m & mask
            if m1 and m1 != mask:
                s.st1 |= ST1_MOV
                if s.st1 & ST1_MOVM: m = s64(0xffff800000000000) if m & 0x8000000000000 else 0x00007fffffffffff
        return m

    def ml(s):
        sfma = (s.st1 >> ST1_SFMA_SH) & 3
        return [s.macc, s.macc << 2, s.macc << 4, s.macc >> 16][sfma]

    def a(s): return (s.aacc << 7) & M32 if s.st1 & ST1_SFAO else s.aacc

    def wa(s, r):
        if r < -2147483648 or r > 2147483647:
            s.st1 |= ST1_AOV
            if s.st1 & ST1_AOVM: r = max(-2147483648, min(2147483647, r))
        s.aacc = r & M32

    # ---- external memory ----
    def xm_init(s):
        adr = s.xoa + s.xba
        m = s.st0 & ST0_M
        mask = 0x0ffff if m == 0 else 0x3ffff if m == 0x10000 else 0xfffff
        adr <<= 2 if s.st0 & ST0_WORD else 1
        if not s.st0 & ST0_SEL: adr <<= 1
        s.xm_adr = adr & mask

    def xm_step(s):
        adr = s.xm_adr; word = s.st0 & ST0_WORD; sel = s.st0 & ST0_SEL
        if word: off = (16 - ((adr & 3) << 3)) if sel else (20 - ((adr & 7) << 2))
        else: off = (16 - ((adr & 1) << 3)) if sel else (20 - ((adr & 3) << 2))
        done = off == 0 if word else off == 8
        if s.reading:
            v = s.xm[adr]
            if sel: s.txrd = (s.txrd & ~(0xff << off)) | (v << off)
            else: s.txrd = (s.txrd & ~(0xf << off)) | ((v & 0xf) << off)
            if done:
                if not word: s.txrd &= 0xffff00
                s.xrd = s.txrd & M24; s.reading = False; s.xm_adr = 0
            else: s.xm_adr = adr + 1
        else:
            s.xm[adr] = ((s.xwr >> off) & (0xff if sel else 0xf))
            if done: s.writing = False; s.xm_adr = 0
            else: s.xm_adr = adr + 1

    # ---- one sample ----
    def sample(s, si=(0, 0, 0, 0)):
        s.si = [v & M24 for v in si]
        s.pc = 0; s.ca = 0; s.id = 0
        if not s.st0 & ST0_INCS: s.ba0 = (s.ba0 - 1) & 0xFF; s.ba1 = (s.ba1 + 1) & 0x1F
        s.xba = (s.xba - 1) & 0x7ffff
        s.st1 &= ~(ST1_AOV | ST1_MOV)
        s.idle = False
        n = 0
        while not s.idle and n < 256:
            s.step(); n += 1
        s.icount += n
        return list(s.so)

    def step(s):
        w = s.prog[s.pc]; ipc = s.pc
        if s.reading or s.writing: s.xm_step()
        s.macc_h4 = s.macc_hist[2]                          # end of t-4
        s.macc_hist = [s.macc, s.macc_write, s.macc_read]   # end of t-1, t-2, t-3 (before this word's shift)
        s.macc_read = s.macc_write; s.macc_write = s.macc
        s.branch = None
        fa = w >> 18; c2 = (w >> 11) & 0x7F
        inc = 0
        if fa == 0x3F:
            s.cat3(c2, w & 0xFF)
        else:
            cprim = bool(w & 0x400)
            if cprim:
                if not w & 0x100 and w & 0x80: inc |= 1
                if w & 0x200: inc |= 2
            else:
                if not w & 0x100 and w & 0x80: inc |= 2
                if w & 0x200: inc |= 1
            if c2 and c2 not in POST: s.cat2(c2, w)
            if fa: s.cat1(fa, w)
            if c2 and c2 in POST: s.cat2(c2, w)
        if inc & 1: s.ca = (s.ca + 1) & 0xFF
        if inc & 2: s.id = (s.id + 1) & 0xFF
        if s.rptc:
            s.rptc -= 1
        elif s.branch is not None:
            s.pc = s.branch
        else:
            s.pc = (s.pc + 1) & 0xFF
        if s.rptc_next: s.rptc = s.rptc_next; s.rptc_next = 0

    # operand helpers for one word
    def cval(s, w):
        if (w & 0x400) and (w & 0x100): return s.get_cmem(w & 0xFF)
        return s.get_cmem(s.ca)

    def cwr(s, w, v):
        a = (w & 0xFF) if (w & 0x400) and (w & 0x100) else s.ca
        s.cmem[a] = v & M32

    def dloc(s, w):
        idx = (w & 0xFF) if (not w & 0x400) and (w & 0x100) else s.id
        if s.st1 & ST1_DBP: return s.dmem1, (idx + s.ba1) & 0x1F
        return s.dmem0, (idx + s.ba0) & 0xFF

    def d24(s, w): m, i = s.dloc(w); return m[i]
    def d(s, w): return (s.d24(w) << 8) & M32
    def dsfai(s, w):
        v = s.d(w)
        return (s32(v) >> 1) & M32 if s.st1 & ST1_SFAI else v
    def dwr(s, w, v): m, i = s.dloc(w); m[i] = v & M24

    def cat3(s, op, i):
        if op == 0x08: s.idle = True
        elif op == 0x10: s.rptc_next = i
        elif op == 0x18: s.ca = i
        elif op == 0x20: s.id = i
        elif op == 0x40:
            if s32(s.aacc) >= 0: s.ca = i
        elif op == 0x48: s.branch = i
        elif op == 0x50:
            if s32(s.aacc) > 0: s.branch = i
        elif op == 0x58:
            if s32(s.aacc) < 0: s.branch = i
        elif op == 0x60:
            if s.aacc: s.branch = i
        elif op == 0x78:
            if s.st1 & ST1_AOV: s.st1 &= ~ST1_AOV; s.branch = i
        else: raise NotImplementedError("cat3 %02x" % op)

    def cat1(s, op, w):
        S = s32
        if op == 0x01:
            v = s.a(); s.aacc = v
            if S(v) < 0:
                s.aacc = (-S(v)) & M32
                if S(s.aacc) < 0: s.st1 |= ST1_AOV
        elif op == 0x02: s.wa(-S(s.a()))
        elif op == 0x03: s.wa(S(s.d(w)) + S(s.a()))
        elif op == 0x04: s.wa(S(s.cval(w)) + S(s.a()))
        elif op in (0x05, 0x06, 0x0b, 0x0c) and s.cat1_lag != 2:    # (unproven stage: see keysdsp.MO1_GAP)
            keep = s.macc_read
            s.macc_read = s.macc_hist[s.cat1_lag - 1] if s.cat1_lag <= 3 else s.macc_h4
            s.cat1_lag, lag = 2, s.cat1_lag
            try: s.cat1(op, w)
            finally: s.cat1_lag = lag; s.macc_read = keep
        elif op == 0x05: s.wa(S(s.dsfai(w)) + (s.mo() >> 16))
        elif op == 0x06: s.wa(S(s.cval(w)) + (s.mo() >> 16))
        elif op == 0x07: s.wa(S(s.d(w)) + S(s.cval(w)))
        elif op == 0x09: s.wa(S(s.d(w)) - S(s.a()))
        elif op == 0x0a: s.wa(S(s.cval(w)) - S(s.a()))
        elif op == 0x0b: s.wa(S(s.dsfai(w)) - (s.mo() >> 16))
        elif op == 0x0c: s.wa(S(s.cval(w)) - (s.mo() >> 16))
        elif op == 0x0d: s.wa(S(s.d(w)) - S(s.cval(w)))
        elif op == 0x10: s.aacc = 0                                    # zacc (empty in MAME)
        elif op == 0x11: s.aacc = s.dsfai(w)
        elif op == 0x12: s.aacc = s.cval(w)
        elif op == 0x14: s.aacc &= s.dsfai(w)
        elif op == 0x15: s.aacc &= s.cval(w)
        elif op == 0x16: d = s.dsfai(w); s.aacc = s.cval(w) & d
        elif op == 0x17: s.aacc |= s.dsfai(w)
        elif op == 0x18: s.aacc |= s.cval(w)
        elif op == 0x19: d = s.dsfai(w); s.aacc = s.cval(w) | d
        elif op in (0x21, 0x24, 0x28, 0x29):                            # mpy / mac / mpyu / macu  d,c
            d = s.d24(w)
            if op in (0x21, 0x24) and d & 0x800000: d -= 1 << 24
            c = s.cval(w); s.creg = c
            r = S(c) * d
            s.macc = (s.ml() if op in (0x24, 0x29) else 0) + (r >> 7)
        elif op in (0x25, 0x2a):                                        # mac / macu  a,d
            d = s.d24(w)
            if op == 0x25 and d & 0x800000: d -= 1 << 24
            c = s.a(); s.creg = c
            s.macc = s.ml() + ((S(c) * d) >> 7)
        elif op in (0x22, 0x26, 0x2e):                                  # mpy / mac / macs  c,a
            c = s.cval(w); s.creg = c
            r = S(c) * S(s.a())
            sh = 14 if op == 0x2e else 15
            s.macc = (0 if op == 0x22 else s.ml()) + (r >> sh)
        elif op == 0x31: s.macc_write = s.macc = S(s.d(w)) << 16
        elif op == 0x32: s.macc_write = s.macc = (s.macc & ~0xffffff) | s.d24(w)
        elif op == 0x33: s.macc_write = s.macc = S(s.cval(w)) << 16
        elif op == 0x34: s.macc = s64((s.macc & 0x8000000000000) | ((s.macc << 1) & 0x7ffffffffffff)) if s.macc >= 0 else s.sfm(1)
        elif op == 0x35: s.macc = s.sfm(-1)
        elif op == 0x38:                                                 # wre d,c
            if s.reading or s.writing: return
            s.xwr = s.d24(w); s.xoa = s.cval(w); s.xm_init(); s.writing = True
        elif op == 0x39:                                                 # rde c
            if s.reading or s.writing: return
            s.xoa = s.cval(w); s.xm_init(); s.reading = True
        else: raise NotImplementedError("cat1 %02x (%s)" % (op, tms57dis.dis(w)))

    def sfm(s, dirn):
        u = s.macc & ((1 << 52) - 1)
        u = (u & 0x8000000000000) | (((u << 1) if dirn > 0 else (u >> 1)) & 0x7ffffffffffff)
        return u - (1 << 52) if u & 0x8000000000000 else u   # MAME keeps it unsigned; sign at bit 51

    def cat2(s, op, w):
        st = s.st1
        if op == 0x01: s.cwr(w, s.a())
        elif op == 0x02: s.dwr(w, s.a() >> 8)
        elif op == 0x03: s.dwr(w, (s.mo() >> 24) & M24)
        elif op == 0x05: s.cwr(w, (s.mo() >> 16) & M32)
        elif op == 0x06: s.dwr(w, (s.mv() >> 24) & 0xffff00)
        elif op == 0x07: s.dwr(w, (s.mv() >> 8) & M24)
        elif op == 0x08: s.ca = (s.a() >> 24) & 0xFF
        elif op == 0x09: s.id = (s.a() >> 24) & 0xFF
        elif op == 0x0e: pass
        elif op == 0x0f: s.dwr(w, s.xrd)
        elif 0x10 <= op <= 0x13: s.dwr(w, s.si[op - 0x10])
        elif 0x1c <= op <= 0x1f: s.so[op - 0x1c] = s.d24(w)             # dos (empty in MAME)
        elif 0x20 <= op <= 0x23: s.so[op - 0x20] = (s.mo() >> 24) & M24
        elif op == 0x31:
            if s.host is None: s.host = s.cval(w)
        elif op == 0x3a: s.st1 &= ~ST1_MOV
        elif op == 0x3c: s.st1 &= ~ST1_AOVM
        elif op == 0x3d: s.st1 |= ST1_AOVM
        elif op == 0x40: s.st1 &= ~ST1_MOVM
        elif op == 0x41: s.st1 |= ST1_MOVM
        elif op == 0x44: s.st1 &= ~ST1_DBP
        elif op == 0x45: s.st1 |= ST1_DBP
        elif 0x48 <= op <= 0x4b: s.st1 = (st & ~(3 << ST1_CRM_SH)) | ((op - 0x48) << ST1_CRM_SH)
        elif op == 0x50: s.st1 &= ~ST1_SFAO
        elif op == 0x51: s.st1 |= ST1_SFAO
        elif op == 0x54: s.st1 &= ~ST1_SFAI
        elif op == 0x55: s.st1 |= ST1_SFAI
        elif 0x58 <= op <= 0x5b: s.st1 = (st & ~(3 << ST1_SFMA_SH)) | ((op - 0x58) << ST1_SFMA_SH)
        elif 0x60 <= op <= 0x63: s.st1 = (st & ~(3 << ST1_SFMO_SH)) | ((op - 0x60) << ST1_SFMO_SH)
        elif 0x68 <= op <= 0x6f: s.st1 = (st & ~(7 << ST1_RND_SH)) | ((op - 0x68) << ST1_RND_SH)
        else: raise NotImplementedError("cat2 %02x (%s)" % (op, tms57dis.dis(w)))


# ---------------- assembler (tms57dis syntax) ----------------
_REV = {}
for (cat, op), txt in tms57dis.TABLE.items():
    key = txt.replace("%d", "D").replace("%c", "C").replace("%i", "I")
    _REV.setdefault(key, (cat, op))


def _operand(tok):
    """'d(1f)' / 'c*' / 'c*+' -> (kind, direct addr | None, inc)."""
    k = tok[0]
    if tok[1] == "(": return k, int(tok[2:-1], 16), False
    return k, None, tok.endswith("+")


def asm(line):
    """One instruction word: up to one cat1 and one cat2 part separated by ';', or one cat3 op."""
    import re
    line = line.split("#")[0].strip()
    if not line or line == "nop": return 0
    parts = [p.strip() for p in line.split(";")]
    fa = c2 = 0; ops = {}; imm = 0
    for p in parts:
        found = [o for o in re.findall(r"\b[dc](?:\([0-9a-fA-F]{1,2}\)|\*\+?)", p)]
        key = re.sub(r"\b([dc])(?:\([0-9a-fA-F]{1,2}\)|\*\+?)", lambda m: m.group(1).upper(), p)
        key = re.sub(r"\s+", " ", key)
        mi = re.match(r"^(\w+) ([0-9a-fA-F]{1,2})$", key)
        if mi and ("%s I" % mi.group(1)) in _REV:
            cat, op = _REV["%s I" % mi.group(1)]; imm = int(mi.group(2), 16)
            assert cat == 3 and len(parts) == 1, line
            return (0x3F << 18) | (op << 11) | imm
        if key not in _REV: raise ValueError("unknown instruction: %r (%s)" % (p, key))
        cat, op = _REV[key]
        if cat == 3:
            assert len(parts) == 1, line
            return (0x3F << 18) | (op << 11)
        if cat == 1:
            assert not fa, line; fa = op
        else:
            assert not c2, line; c2 = op
        for o in found:
            k, adr, inc = _operand(o)
            if k in ops: assert ops[k] == (adr, inc), "operand %s used twice differently: %s" % (k, line)
            ops[k] = (adr, inc)
    w = (fa << 18) | (c2 << 11)
    cd, dd = ops.get("c"), ops.get("d")
    if cd and cd[0] is not None:
        assert not (dd and dd[0] is not None), "two direct operands: " + line
        w |= 0x400 | 0x100 | cd[0]
        if dd and dd[1]: w |= 0x200
    elif dd and dd[0] is not None:
        w |= 0x100 | dd[0]
        if cd and cd[1]: w |= 0x200
    else:                                                # both indirect (or absent): d primary
        if dd and dd[1]: w |= 0x80
        if cd and cd[1]: w |= 0x200
    return w


def assemble(lines):
    return [asm(l) for l in lines]


def selftest():
    # 1) every word of Sony's captured program round-trips dis -> asm
    p = os.path.join(os.path.dirname(__file__), "..", "out", "dsp", "tms57002_program.txt")
    n = bad = 0
    if os.path.exists(p):
        for l in open(p, encoding="utf-8").read().splitlines()[1:]:
            f = l.split()
            if len(f) < 3 or not f[0].isdigit(): break
            w = int(f[1], 16); t = tms57dis.dis(w); n += 1
            try:
                w2 = asm(t)
            except Exception as e:
                bad += 1; print("asm fail %06X %s: %s" % (w, t, e)); continue
            if tms57dis.dis(w2) != t: bad += 1; print("mismatch %06X %s -> %06X %s" % (w, t, w2, tms57dis.dis(w2)))
        print("round-trip: %d words, %d bad" % (n, bad))
    # 2) tiny program (Sony's ST0/ST1): so0_l = si0_l * 0.5 * 4 (sfmo 2) via the mac pipeline, so0_r = delay-RAM echo of si0_l 3 samples back
    prog = assemble([
        "dis si0_l,d(00)",          # 0
        "mpy d(00),c*",             # 1  macc = x * c0 (ca = 0)
        "nop",                      # 2
        "domh so0_l",               # 3  sees macc of word 1
        "wre d*,c(01)",             # 4  write x (id = 0) at xoa 0
        "nop", "nop", "nop",        # 5-7
        "rde c(02)",                # 8  read xoa 3 (3 samples ago)
        "nop", "nop", "nop",        # 9-11
        "srbd d(01)",               # 12
        "dos so0_r,d(01)",          # 13
        "idle",
    ])
    t = TMS()
    t.load(0x010820, 0x000820, prog, {0: 0x40000000, 1: 0, 2: 3})
    outs = []
    for k in range(8):
        x = (k + 1) * 0x10000
        outs.append(t.sample((x, 0, 0, 0)))
    l = [o[0] for o in outs]; r = [o[1] for o in outs]
    exp_l = [((k + 1) * 0x10000) * 2 for k in range(8)]      # x * 0.5, then sfmo 2 (ST1 0x820) = x4
    exp_r = [0, 0, 0] + [((k - 2) * 0x10000) & 0xffff00 for k in range(3, 8)]
    print("halve:", "OK" if l == exp_l else "BAD %s" % l)
    print("echo3:", "OK" if r == exp_r else "BAD %s vs %s" % ([hex(v) for v in r], [hex(v) for v in exp_r]))


if __name__ == "__main__":
    if "--selftest" in sys.argv: selftest()
