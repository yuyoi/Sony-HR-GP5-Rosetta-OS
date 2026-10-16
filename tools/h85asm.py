# Small two-pass H8/500 assembler (maximum mode) for the HR-GP5 Rosetta OS.
# Syntax (Hitachi-ish):  label:   MOV.B #0x12,R0   MOV.W @(4,R1),R2   BSET.B #3,@0xFD01   JSR @label
#   ea: Rn  @Rn  @Rn+  @-Rn  @(d,Rn)  @addr (16-bit absolute, DP page)  #imm
#   directives: .org A   .equ N,V   .db 1,2,"txt"   .dw A,label   .asciz "txt"   .align 2
#   expressions: 123 0x1F H'1F 'c' label  + - * / & | >> <<  hi(x) lo(x)
# Every instruction is checked by disassembling it back (h8500dis) and comparing length.
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from h8500dis import Dis

REG = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5, "R6": 6, "FP": 6, "R7": 7, "SP": 7}
CREG = {"SR": 0, "CCR": 1, "BR": 3, "EP": 4, "DP": 5, "TP": 7}
BCC = ["BRA", "BRN", "BHI", "BLS", "BCC", "BCS", "BNE", "BEQ", "BVC", "BVS", "BPL", "BMI", "BGE", "BLT", "BGT", "BLE"]
BCC_ALIAS = {"BT": "BRA", "BF": "BRN", "BHS": "BCC", "BLO": "BCS"}
UNARY = ["SWAP", "EXTS", "EXTU", "CLR", "NEG", "NOT", "TST", "TAS", "SHAL", "SHAR", "SHLL", "SHLR", "ROTL", "ROTR", "ROTXL", "ROTXR"]
ALU = {"ADD": 0x20, "SUB": 0x30, "OR": 0x40, "AND": 0x50, "XOR": 0x60, "CMP": 0x70, "MOV": 0x80,
       "ADDX": 0xA0, "MULXU": 0xA8, "SUBX": 0xB0, "DIVXU": 0xB8}
BITOP = {"BSET": 0, "BCLR": 1, "BNOT": 2, "BTST": 3}


class AsmError(Exception): pass


class Asm:
    def __init__(s, syms=None):
        s.syms = dict(syms or {})
        s.out = {}            # addr -> byte

    # ---------- expressions ----------
    def val(s, e, final):
        e = e.strip()
        e = re.sub(r"H'([0-9A-Fa-f]+)", r"0x\1", e)
        e = re.sub(r"'(.)'", lambda m: str(ord(m.group(1))), e)
        def name(m):
            n = m.group(0)
            if n[0].isdigit() or n in ("hi", "lo"): return n
            if n in s.syms: return str(s.syms[n])
            if final: raise AsmError("unknown symbol " + n)
            return "0"
        e2 = re.sub(r"0[xX][0-9A-Fa-f]+|\d+|[A-Za-z_][A-Za-z0-9_\.]*", name, e)
        try:
            return int(eval(e2, {"__builtins__": {}}, {"hi": lambda v: (v >> 8) & 0xFF, "lo": lambda v: v & 0xFF}))
        except Exception as ex:
            raise AsmError("bad expression %r (%s)" % (e, ex))

    # ---------- operands ----------
    def ea(s, t, w, final):
        """-> (ea bytes, kind) for general format"""
        t = t.strip()
        if t.upper() in REG: return bytes([0xA0 | (8 if w else 0) | REG[t.upper()]]), "reg"
        m = re.fullmatch(r"@-(\w+)", t)
        if m: return bytes([0xB0 | (8 if w else 0) | REG[m.group(1).upper()]]), "mem"
        m = re.fullmatch(r"@(\w+)\+", t)
        if m and m.group(1).upper() in REG: return bytes([0xC0 | (8 if w else 0) | REG[m.group(1).upper()]]), "mem"
        m = re.fullmatch(r"@(\w+)", t)
        if m and m.group(1).upper() in REG: return bytes([0xD0 | (8 if w else 0) | REG[m.group(1).upper()]]), "mem"
        m = re.fullmatch(r"@\((.+),\s*(\w+)\)", t)
        if m and m.group(2).upper() in REG:
            d = s.val(m.group(1), final); n = REG[m.group(2).upper()]
            if -128 <= d <= 127: return bytes([0xE0 | (8 if w else 0) | n, d & 0xFF]), "mem"
            return bytes([0xF0 | (8 if w else 0) | n, (d >> 8) & 0xFF, d & 0xFF]), "mem"
        m = re.fullmatch(r"@(.+)", t)
        if m:
            a = s.val(m.group(1), final) & 0xFFFF
            return bytes([0x1D if w else 0x15, a >> 8, a & 0xFF]), "mem"
        raise AsmError("bad operand " + t)

    def imm(s, t, w, final):
        v = s.val(t[1:], final)
        return bytes([(v >> 8) & 0xFF, v & 0xFF]) if w else bytes([v & 0xFF])

    # ---------- one instruction ----------
    def enc(s, pc, mn, ops, final):
        up = mn.upper()
        base, _, sz = up.partition(".")
        w = sz == "W"
        base = BCC_ALIAS.get(base, base)
        n = len(ops)
        isreg = lambda t: t.strip().upper() in REG
        r = lambda t: REG[t.strip().upper()]
        if base == "NOP": return b"\x00"
        if base == "RTS": return b"\x19"
        if base == "RTE": return b"\x0A"
        if base == "PRTS": return b"\x11\x19"
        if base == "SLEEP": return b"\x1A"
        if base in BCC or base == "BSR":
            t = s.val(ops[0], final)
            d8 = t - (pc + 2)
            code = 0x0E if base == "BSR" else 0x20 | BCC.index(base)
            if sz != "16" and -128 <= d8 <= 127 and not s.force16.get(pc):
                return bytes([code, d8 & 0xFF])
            d16 = t - (pc + 3)
            return bytes([code | 0x10, (d16 >> 8) & 0xFF, d16 & 0xFF])
        if base in ("JMP", "JSR"):
            t = ops[0].strip()
            m = re.fullmatch(r"@(\w+)", t)
            if m and m.group(1).upper() in REG:
                return bytes([0x11, (0xD8 if base == "JSR" else 0xD0) | REG[m.group(1).upper()]])
            a = s.val(t.lstrip("@"), final) & 0xFFFF
            return bytes([0x18 if base == "JSR" else 0x10, a >> 8, a & 0xFF])
        if base in ("PJMP", "PJSR"):
            a = s.val(ops[0].lstrip("@"), final)
            return bytes([0x13 if base == "PJMP" else 0x03, (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF])
        if base in ("STM", "LDM"):
            lst = ops[0] if base == "STM" else ops[1]
            bits = 0
            for part in lst.strip("() ").split(","):
                if "-" in part:
                    a_, b_ = part.split("-"); bits |= sum(1 << i for i in range(r(a_), r(b_) + 1))
                else: bits |= 1 << r(part)
            return bytes([0x12 if base == "STM" else 0x02, bits])
        if base in ("LDC", "ORC", "ANDC", "XORC") and n == 2 and ops[1].strip().upper() in CREG:
            c = CREG[ops[1].strip().upper()]
            if ops[0].strip().startswith("#"):
                code = {"LDC": 0x88, "ORC": 0x48, "ANDC": 0x58, "XORC": 0x68}[base]
                return bytes([0x0C if w else 0x04]) + s.imm(ops[0], w, final) + bytes([code | c])
            e, _ = s.ea(ops[0], w, final)
            return e + bytes([0x88 | c])
        if base == "STC":
            c = CREG[ops[0].strip().upper()]
            e, _ = s.ea(ops[1], w, final)
            return e + bytes([0x98 | c])
        if base == "XCH": return bytes([0xA8 | r(ops[0]), 0x90 | r(ops[1])])
        if base in UNARY:
            e, _ = s.ea(ops[0], w, final)
            return e + bytes([0x10 | UNARY.index(base)])
        if base in BITOP:
            e, _ = s.ea(ops[1], w, final)
            k = BITOP[base]
            if ops[0].strip().startswith("#"):
                return e + bytes([0xC0 | (k << 4) | (s.val(ops[0][1:], final) & 15)])
            return e + bytes([0x40 | (k << 4) | r(ops[0])])
        if base in ALU and n == 2:
            src, dst = ops[0].strip(), ops[1].strip()
            if src.startswith("#") and isreg(dst):
                if base == "MOV": return bytes([(0x58 if w else 0x50) | r(dst)]) + s.imm(src, w, final)
                if base == "CMP": return bytes([(0x48 if w else 0x40) | r(dst)]) + s.imm(src, w, final)
                return bytes([0x0C if w else 0x04]) + s.imm(src, w, final) + bytes([ALU[base] | r(dst)])
            if src.startswith("#"):                       # #imm -> memory (MOV:G / CMP:G)
                if base not in ("MOV", "CMP"): raise AsmError("only MOV/CMP #imm,<mem>")
                e, _ = s.ea(dst, w, final)
                v = s.val(src[1:], final)
                big = w and not (-128 <= (v if v < 0x8000 else v - 0x10000) <= 127)
                code = (0x06 if base == "MOV" else 0x04) | (1 if big else 0)
                return e + bytes([code]) + (bytes([(v >> 8) & 0xFF, v & 0xFF]) if big else bytes([v & 0xFF]))
            if isreg(dst) and base != "MOV" or (base == "MOV" and isreg(dst)):
                e, _ = s.ea(src, w, final)
                return e + bytes([ALU[base] | r(dst)])
            if base == "MOV" and isreg(src):                # store
                e, _ = s.ea(dst, w, final)
                return e + bytes([0x90 | r(src)])
        if base in ("ADDS", "SUBS") and n == 2:
            e, _ = s.ea(ops[0], w, final)
            return e + bytes([(0x28 if base == "ADDS" else 0x38) | r(ops[1])])
        raise AsmError("can't encode: %s %s" % (mn, ",".join(ops)))

    # ---------- passes ----------
    def split_ops(s, t):
        out, depth, cur, q = [], 0, "", False
        for ch in t:
            if ch == '"': q = not q
            if ch == "(" and not q: depth += 1
            if ch == ")" and not q: depth -= 1
            if ch == "," and depth == 0 and not q: out.append(cur); cur = ""
            else: cur += ch
        if cur.strip(): out.append(cur)
        return [o.strip() for o in out]

    def assemble(s, text):
        lines = text.splitlines()
        s.force16 = {}
        for p in range(6):
            final = p >= 4
            s.out = {}; s.lst = []
            pc = 0
            defined = set()
            for ln, raw in enumerate(lines, 1):
                line = raw.split(";")[0].rstrip() if '"' not in raw else re.sub(r';(?=(?:[^"]*"[^"]*")*[^"]*$).*', "", raw).rstrip()
                if not line.strip(): continue
                m = re.match(r"\s*([A-Za-z_][\w\.]*):(.*)", line)
                if m:
                    lab = m.group(1)
                    if lab in defined:
                        raise AsmError("line %d: label %s defined twice" % (ln, lab))
                    defined.add(lab)
                    s.syms[lab] = pc
                    line = m.group(2)
                    if not line.strip(): continue
                parts = line.strip().split(None, 1)
                mn, rest = parts[0], (parts[1] if len(parts) > 1 else "")
                try:
                    if mn == ".org": pc = s.val(rest, final); continue
                    if mn == ".equ":
                        k, v = rest.split(",", 1); s.syms[k.strip()] = s.val(v, final); continue
                    if mn == ".align": a = s.val(rest, final); pc = (pc + a - 1) // a * a; continue
                    if mn in (".db", ".asciz", ".ascii", ".dw"):
                        data = bytearray()
                        for o in s.split_ops(rest):
                            if o.startswith('"'): data += o[1:-1].encode("latin1")
                            elif mn == ".dw": v = s.val(o, final); data += bytes([(v >> 8) & 0xFF, v & 0xFF])
                            else: data.append(s.val(o, final) & 0xFF)
                        if mn == ".asciz": data.append(0)
                        b = bytes(data)
                    else:
                        b = s.enc(pc, mn, s.split_ops(rest), final)
                        if final:
                            i = Dis(bytes(512)).dec  # placeholder to keep import
                except AsmError as ex:
                    raise AsmError("line %d: %s  (%s)" % (ln, ex, raw.strip()))
                for k, x in enumerate(b): s.out[pc + k] = x
                s.lst.append((pc, b, raw.strip(), mn))
                pc += len(b)
        return s.out

    def check(s, image, base_page):
        """disassemble every instruction from the image and compare lengths"""
        d = Dis(image)
        bad = []
        for pc, b, src, mn in s.lst:
            if mn.startswith("."): continue
            i = d.dec(base_page | pc)
            if i.len != len(b) or i.kind == "bad": bad.append((pc, b.hex(" "), src, i.text))
        return bad

    def listing(s, image, base_page):
        d = Dis(image)
        out = []
        for pc, b, src, mn in s.lst:
            dis = "" if mn.startswith(".") else d.dec(base_page | pc).text
            out.append("%05X: %-18s %-40s %s" % (base_page | pc, b[:8].hex(" "), src[:40], dis))
        return "\n".join(out)
