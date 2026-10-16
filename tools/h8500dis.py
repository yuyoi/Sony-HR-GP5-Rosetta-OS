# license:BSD-3-Clause
# copyright-holders:AJR (MAME h8500dasm.cpp); Python port + code tracing: JSW and Claude
# Hitachi H8/500 disassembler (maximum/expanded mode), Python port of MAME's
# h8500dasm.cpp plus recursive code tracing for the HR-GP5 ROM.
import struct, sys

GEN = [None, None, None, None, "ADD", "ADDS", "SUB", "SUBS", "OR", None, "AND", None,
       "XOR", None, "CMP", None, "MOV", "LDC", "MOV", "STC", "ADDX", "MULXU", "SUBX", "DIVXU"] + [None] * 8
BITOPS = ["BSET", "BCLR", "BNOT", "BTST"]
UNARY = ["SWAP", "EXTS", "EXTU", "CLR", "NEG", "NOT", "TST", "TAS",
         "SHAL", "SHAR", "SHLL", "SHLR", "ROTL", "ROTR", "ROTXL", "ROTXR"]
BRANCH = ["BRA", "BRN", "BHI", "BLS", "BCC", "BCS", "BNE", "BEQ",
          "BVC", "BVS", "BPL", "BMI", "BGE", "BLT", "BGT", "BLE"]


def s8(x): return x - 0x100 if x & 0x80 else x
def s16(x): return x - 0x10000 if x & 0x8000 else x
def bit(x, n): return (x >> n) & 1


def reg(n): return "FP" if n == 6 else "SP" if n == 7 else "R%d" % n


def creg(n, w):
    if n == 0 and w: return "SR"
    return {1: "CCR", 3: "BR", 4: "EP", 5: "DP", 7: "TP"}.get(n, "CR%d" % n)


def reglist(x):
    out, start = [], -1
    for n in range(9):
        if start == -1 and bit(x, n): start = n
        elif start != -1 and not bit(x, n):
            out.append(reg(start) + ("" if n - 1 == start else "-" + reg(n - 1)))
            start = -1
    return "(" + ",".join(out) + ")"


class Insn:
    __slots__ = ("pc", "len", "text", "kind", "target", "mem", "imm")

    def __init__(s, pc):
        s.pc, s.len, s.text, s.kind, s.target, s.mem, s.imm = pc, 1, "", "seq", None, None, None


class Dis:
    def __init__(s, data, base=0):
        s.d, s.base = data, base

    def r8(s, a):
        a -= s.base
        return s.d[a] if 0 <= a < len(s.d) else 0

    def r16(s, a): return (s.r8(a) << 8) | s.r8(a + 1)

    def ea_str(s, i, ea, disp):
        if ea >= 0xe0:
            r = reg(ea & 7)
            if ea >= 0xf0: return "@(H'%04X,%s)" % (disp & 0xffff, r)
            if -9 <= disp <= 9: return "@(%d,%s)" % (disp, r)
            return "@(-H'%02X,%s)" % (-disp, r) if disp < 0 else "@(H'%02X,%s)" % (disp, r)
        if ea >= 0xb0:
            hi = ea & 0xf0
            return "@" + ("-" if hi == 0xb0 else "") + reg(ea & 7) + ("+" if hi == 0xc0 else "")
        if ea >= 0xa0: return reg(ea & 7)
        if bit(ea, 4):
            i.mem = disp & 0xffff
            return "@H'%04X:16" % (disp & 0xffff)
        i.mem = 0x10000 | (disp & 0xff)  # flag: short absolute (BR:aa)
        return "@H'%02X:8" % (disp & 0xff)

    def dec(s, pc):
        i = Insn(pc)
        op = s.r8(pc)
        page = pc & ~0xffff
        if op >= 0xa0 or (op & 0xe7) == 0x05:
            s.general(i, pc, op)
        elif op >= 0x60:
            w = bit(op, 3)
            disp = s8(s.r8(pc + 1))
            ea = s.ea_str(i, (0xe6 if op >= 0x80 else 0x05) | (op & 8), disp)
            sz = "W" if w else "B"
            i.text = ("MOV.%s %s, %s" % (sz, reg(op & 7), ea)) if bit(op, 4) else ("MOV.%s %s, %s" % (sz, ea, reg(op & 7)))
            i.len = 2
        elif op >= 0x40:
            w = bit(op, 3)
            imm = s.r16(pc + 1) if w else s.r8(pc + 1)
            i.imm = imm
            i.text = "%s.%s #H'%0*X, %s" % ("MOV" if bit(op, 4) else "CMP", "W" if w else "B", 4 if w else 2, imm, reg(op & 7))
            i.len = 3 if w else 2
        elif op >= 0x20:
            if bit(op, 4):
                disp, i.len = s16(s.r16(pc + 1)), 3
            else:
                disp, i.len = s8(s.r8(pc + 1)), 2
            i.target = page | ((pc + i.len + disp) & 0xffff)
            c = op & 0xf
            i.kind = "jmp" if c == 0 else "seq" if c == 1 else "bcc"
            if c == 1: i.target = None
            i.text = "%s H'%05X" % (BRANCH[c], page | ((pc + i.len + disp) & 0xffff))
        elif op == 0x00:
            i.text = "NOP"
        elif op in (0x03, 0x13):
            t = (s.r8(pc + 1) << 16) | s.r16(pc + 2)
            i.text, i.len, i.target = "%s @H'%06X" % ("PJMP" if bit(op, 4) else "PJSR", t), 4, t
            i.kind = "jmp" if bit(op, 4) else "call"
        elif op in (0x01, 0x06, 0x07, 0x11):
            s.misc(i, pc, op)
        elif op == 0x02:
            i.text, i.len = "LDM.W @SP+, " + reglist(s.r8(pc + 1)), 2
        elif op in (0x04, 0x0c):
            s.immop(i, pc, bit(op, 3))
        elif op == 0x08:
            v = s.r8(pc + 1)
            i.text, i.len = "TRAPA " + ("#H'%02X" % (v & 15) if (v & 0xf0) == 0x10 else "illegal"), 2
            if (v & 0xf0) != 0x10: i.kind = "bad"
        elif op == 0x09:
            i.text = "TRAP/VS"
        elif op == 0x0a:
            i.text, i.kind = "RTE", "ret"
        elif op in (0x0e, 0x1e):
            if bit(op, 4): disp, i.len = s16(s.r16(pc + 1)), 3
            else: disp, i.len = s8(s.r8(pc + 1)), 2
            i.target = page | ((pc + i.len + disp) & 0xffff)
            i.text, i.kind = "BSR H'%05X" % i.target, "call"
        elif op == 0x0f:
            i.text = "UNLK FP"
        elif op in (0x10, 0x18):
            a = s.r16(pc + 1)
            i.target = page | a
            i.text, i.len = "%s @H'%04X" % ("JSR" if bit(op, 3) else "JMP", a), 3
            i.kind = "call" if bit(op, 3) else "jmp"
        elif op == 0x12:
            i.text, i.len = "STM.W %s, @-SP" % reglist(s.r8(pc + 1)), 2
        elif op in (0x14, 0x1c):
            if bit(op, 3): i.text, i.len = "RTD #H'%04X" % s.r16(pc + 1), 3
            else: i.text, i.len = "RTD #H'%02X" % s.r8(pc + 1), 2
            i.kind = "ret"
        elif op in (0x17, 0x1f):
            if bit(op, 3): i.text, i.len = "LINK FP, #H'%04X" % s.r16(pc + 1), 3
            else: i.text, i.len = "LINK FP, #H'%02X" % s.r8(pc + 1), 2
        elif op == 0x19:
            i.text, i.kind = "RTS", "ret"
        elif op == 0x1a:
            i.text = "SLEEP"
        else:
            s.illegal(i, op)
        return i

    def illegal(s, i, op):
        i.text, i.len, i.kind = ".DATA.B H'%02X" % op, 1, "bad"

    def general(s, i, pc, ea):
        eab, disp = 1, 0
        if ea >= 0xe0 or (ea & 0xe7) == 0x05:
            if bit(ea, 4): eab, disp = 3, s16(s.r16(pc + 1))
            else: eab, disp = 2, s8(s.r8(pc + 1))
        w = bit(ea, 3)
        sz = "W" if w else "B"
        op = s.r8(pc + eab)
        if (ea & 0xf8) == 0xa8 and (op & 0xf8) == 0x90:
            i.text, i.len = "XCH %s, %s" % (reg(ea & 7), reg(op & 7)), eab + 1
            return
        if GEN[op >> 3] is not None:
            e = s.ea_str(i, ea, disp)
            t = "%s.%s " % (GEN[op >> 3], sz)
            if (op & 0xf0) == 0x90:
                t += (creg(op & 7, w) if bit(op, 3) else reg(op & 7)) + ", " + e
            else:
                t += e + ", " + (creg(op & 7, w) if (op & 0xf8) == 0x88 else reg(op & 7))
            i.text, i.len = t, eab + 1
            return
        if bit(op, 6):
            e = s.ea_str(i, ea, disp)
            src = "#%d" % (op & 15) if bit(op, 7) else reg(op & 7)
            i.text, i.len = "%s.%s %s, %s" % (BITOPS[(op >> 4) & 3], sz, src, e), eab + 1
            return
        if (op & 0xf0) == 0x10 and ((ea & 0xf8) == 0xa0 or op >= 0x13):
            i.text, i.len = "%s.%s %s" % (UNARY[op & 15], sz, s.ea_str(i, ea, disp)), eab + 1
            return
        if (op & 0xfc) == 0x04 and (ea & 0xf0) != 0xa0:
            e = s.ea_str(i, ea, disp)
            imm = s.r16(pc + eab + 1) if bit(op, 0) else s.r8(pc + eab + 1)
            i.imm = imm
            i.text = "%s.%s #H'%0*X, %s" % ("MOV" if bit(op, 1) else "CMP", sz, 4 if bit(op, 0) else 2, imm, e)
            i.len = eab + (3 if bit(op, 0) else 2)
            return
        if (op & 0xfa) == 0x08:
            i.text = "ADD.%s #%s%d, %s" % (sz, "-" if bit(op, 2) else "", 2 if bit(op, 0) else 1, s.ea_str(i, ea, disp))
            i.len = eab + 1
            return
        if op == 0x00:
            op2 = s.r8(pc + eab + 1)
            if (op2 & 0xa8) == 0x80 and (ea & 0xf0) != 0xa0:
                e = s.ea_str(i, ea, disp)
                if bit(op2, 4): i.text = "MOVTPE.%s %s, %s" % (sz, reg(op2 & 7), e)
                else: i.text = "MOVFPE.%s %s, %s" % (sz, e, reg(op2 & 7))
                i.len = eab + 2
                return
            if (op2 & 0xe8) == 0xa0 and (ea & 0xf8) == 0xa0:
                i.text = "%s %s, %s" % ("DSUB.B" if bit(op2, 4) else "DADD.B", reg(ea & 7), reg(op2 & 7))
                i.len = eab + 2
                return
        s.illegal(i, ea)

    def misc(s, i, pc, ea):
        op = s.r8(pc + 1)
        page = pc & ~0xffff
        if ea == 0x11 and op >= 0xd0:
            eab, disp = 1, 0
            if op >= 0xe0:
                if bit(op, 4): eab, disp = 3, s16(s.r16(pc + 2))
                else: eab, disp = 2, s8(s.r8(pc + 2))
            i.text = "%s %s" % ("JSR" if bit(op, 3) else "JMP", s.ea_str(i, op, disp))
            i.len = eab + 1
            i.kind = "icall" if bit(op, 3) else "ijmp"
            i.mem = disp & 0xffff if op >= 0xf0 else None  # table base for @(d16,Rn)
        elif ea == 0x11 and op >= 0xc0:
            i.text, i.len = "%s @%s" % ("PJSR" if bit(op, 3) else "PJMP", reg(op & 7)), 2
            i.kind = "icall" if bit(op, 3) else "ijmp"
        elif (op & 0xf8) == 0xb8:
            cond = {1: "F", 6: "NE", 7: "EQ"}.get(ea, "?")
            i.len = 3
            i.target = page | ((pc + 3 + s8(s.r8(pc + 2))) & 0xffff)
            i.text, i.kind = "SCB/%s %s, H'%05X" % (cond, reg(op & 7), i.target), "bcc"
        elif ea == 0x11 and (op & 0xf7) == 0x14:
            if bit(op, 3): i.text, i.len = "PRTD #H'%04X" % s.r16(pc + 2), 4
            else: i.text, i.len = "PRTD #H'%02X" % s.r8(pc + 2), 3
            i.kind = "ret"
        elif ea == 0x11 and op == 0x19:
            i.text, i.len, i.kind = "PRTS", 2, "ret"
        else:
            s.illegal(i, ea)

    def immop(s, i, pc, w):
        imm = s.r16(pc + 1) if w else s.r8(pc + 1)
        op = s.r8(pc + (3 if w else 2))
        sz = "W" if w else "B"
        g = GEN[op >> 3]
        ok = (op & 0xf0) != 0x90 if g is not None else ((op & 0xc8) == 0x48 and op < 0x70)
        if not ok:
            return s.illegal(i, 0x0c if w else 0x04)
        name = (GEN[(op & 0xf0) >> 3] + "C") if (op & 0xc8) == 0x48 else g
        dst = creg(op & 7, w) if (bit(op, 3) and 0x48 <= op < 0x90) else reg(op & 7)
        i.imm = imm
        i.text, i.len = "%s.%s #H'%0*X, %s" % (name, sz, 4 if w else 2, imm, dst), 4 if w else 3


def trace(dis, entries, size):
    """Recursive descent from entry points. Returns dict pc->Insn, set of call targets, jump-table hits."""
    code, calls, todo, seen = {}, set(), list(entries), set()
    labels = set(entries)
    while todo:
        pc = todo.pop()
        while 0 <= pc < size and pc not in seen:
            i = dis.dec(pc)
            if i.kind == "bad":
                break
            for k in range(i.len): seen.add(pc + k)
            code[pc] = i
            if i.target is not None and i.target < size:
                labels.add(i.target)
                todo.append(i.target)
                if i.kind == "call": calls.add(i.target)
            if i.kind in ("jmp", "ret", "ijmp"):
                break
            pc += i.len
    return code, calls, labels


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    d = Dis(data)
    pc = int(sys.argv[2], 16)
    for _ in range(int(sys.argv[3]) if len(sys.argv) > 3 else 40):
        i = d.dec(pc)
        print("%05X: %-14s %s" % (pc, data[pc:pc + i.len].hex(" "), i.text))
        pc += i.len
