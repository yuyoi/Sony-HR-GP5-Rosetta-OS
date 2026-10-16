# HR-GP5 firmware analysis: traces code from the vectors, the UI dispatch tables (sub_00672 rows of 13 handlers)
# and other found entry points to a fixpoint; annotates string refs, RAM vars and known symbols.
#   python -I tools/gp5re.py dumps/hrgp5_eprom_read1.bin out/      -> out/hrgp5.lst, out/tables.txt, out/ram.txt
# Your listing is Sony code: keep out/ to yourself (it's gitignored).
import sys, os, re, struct, collections
sys.path.insert(0, os.path.dirname(__file__))
from h8500dis import Dis
from symbols import SYMS, RAM

DISPATCH = 0x0672


def r16(rom, a): return (rom[a] << 8) | rom[a + 1]


def cstr(rom, a, minlen=3):
    """printable C string at a, or None"""
    if not (0 <= a < len(rom)): return None
    e = a
    while e < len(rom) and e - a < 64 and (32 <= rom[e] < 127 or rom[e] in (0x7E,)):
        e += 1
    if e - a >= minlen and e < len(rom) and rom[e] == 0:
        return rom[a:e].decode("ascii")
    return None


class Tracer:
    def __init__(s, rom):
        s.rom, s.d = rom, Dis(rom)
        s.code, s.calls, s.labels, s.seen = {}, set(), set(), set()
        s.tables = {}          # table addr -> (rows, caller)
        s.extra = {}           # addr -> why

    def run(s, entries):
        todo = list(entries)
        size = len(s.rom)
        while todo:
            pc = todo.pop()
            if pc in s.seen: continue
            s.labels.add(pc)
            while 0 <= pc < size and pc not in s.seen:
                i = s.d.dec(pc)
                if i.kind == "bad": break
                for k in range(i.len): s.seen.add(pc + k)
                s.code[pc] = i
                if i.target is not None and i.target < size:
                    s.labels.add(i.target); todo.append(i.target)
                    if i.kind == "call": s.calls.add(i.target)
                if i.kind in ("jmp", "ret", "ijmp"): break
                pc += i.len
        return s

    def find_tables(s):
        """MOV.W #T,R0 ... JMP @H'0672 -> dispatch table T (rows of 13 words until the first handler)."""
        new = []
        insns = sorted(s.code)
        for idx, pc in enumerate(insns):
            i = s.code[pc]
            if i.text == "JMP @H'%04X" % DISPATCH:
                for back in insns[max(0, idx - 6):idx][::-1]:
                    m = re.match(r"MOV\.W #H'([0-9A-F]{4}), R0", s.code[back].text)
                    if m:
                        t = (pc & ~0xFFFF) | int(m.group(1), 16)
                        if t in s.tables: break
                        hs = [(t & ~0xFFFF) | r16(s.rom, t + 2 * k) for k in range(13)]
                        after = [h for h in hs if h > t]
                        rows = max(1, (min(after) - t) // 26) if after else 1
                        rows = min(rows, 8)
                        allh = []
                        for r in range(rows):
                            allh += [(t & ~0xFFFF) | r16(s.rom, t + 26 * r + 2 * k) for k in range(13)]
                        s.tables[t] = (rows, pc, allh)
                        new += allh
                        break
        return new


def analyse(rom):
    vec = [struct.unpack(">I", rom[v:v + 4])[0] for v in range(0, 0x100, 4)]
    entries = set(a for a in vec if a) | set(a for a in SYMS if SYMS[a][0].startswith(("sub_", "f_", "h_")) or True)
    entries = {a for a in entries if 0x180 <= a < len(rom)}
    t = Tracer(rom).run(sorted(entries))
    while True:
        new = [h for h in t.find_tables() if h not in t.seen]
        if not new: break
        t.run(new)
    return t, vec


def ram_name(dp, a):
    key = ((dp if dp is not None else -1) << 16) | a
    if dp is None:
        for k in ((0 << 16) | a, (4 << 16) | a):
            if k in RAM: return RAM[k]
        return None
    return RAM.get(key)


def write_listing(t, out):
    rom = t.rom
    size = len(rom)
    names = {}
    for a in t.labels:
        names[a] = ("sub_%05X" if a in t.calls else "loc_%05X") % a
    for a, (n, c) in SYMS.items(): names[a] = n
    xref = collections.defaultdict(list)
    for pc, i in t.code.items():
        if i.target is not None: xref[i.target].append(pc)
    for tb, (rows, caller, hs) in t.tables.items():
        for k, h in enumerate(hs): xref[h].append(tb + 2 * k)
    ram = collections.defaultdict(lambda: [set(), set()])   # (dp,a) -> [readers, writers]
    strs = {}
    with open(os.path.join(out, "hrgp5.lst"), "w", encoding="utf-8") as f:
        pc, dp = 0, None
        while pc < size:
            if pc in t.tables:
                rows, caller, hs = t.tables[pc]
                f.write("\nui_table_%05X:   ; %d page(s), JMP 0672 at %05X\n" % (pc, rows, caller))
                for r in range(rows):
                    f.write("  %05X: dw %s\n" % (pc + 26 * r, " ".join(names.get(h, "%04X" % (h & 0xFFFF)) for h in hs[13 * r:13 * r + 13])))
                pc += 26 * rows
                continue
            if pc in t.code:
                i = t.code[pc]
                if pc in names:
                    dp = None
                    xr = xref.get(pc, [])
                    c = SYMS.get(pc, ("", ""))[1]
                    f.write("\n%s:%s%s\n" % (names[pc], ("   ; " + c) if c else "",
                                              ("   ; xref " + " ".join("%05X" % x for x in sorted(xr)[:8]) + (" ..." if len(xr) > 8 else "")) if xr else ""))
                txt = i.text
                if i.target is not None and i.target in names:
                    txt = re.sub(r"H'[0-9A-F]{4,6}$", names[i.target], txt) if i.kind != "seq" else txt
                    txt = re.sub(r"@H'[0-9A-F]{4,6}$", names[i.target], txt)
                note = []
                m = re.match(r"LDC\.B #H'([0-9A-F]{2}), DP", i.text)
                if m: dp = int(m.group(1), 16)
                for mm in re.finditer(r"@H'([0-9A-F]{4}):16", i.text):
                    a = int(mm.group(1), 16)
                    nm = ram_name(dp, a)
                    if nm: note.append(nm)
                    w = i.text.split()[0].startswith(("MOV", "CLR", "BSET", "BCLR", "BNOT", "ADD", "SUB")) and i.text.rstrip().endswith(":16")
                    ram[(dp, a)][1 if w else 0].add(pc)
                m = re.search(r"#H'([0-9A-F]{4}), R[0-7]", i.text)
                if m:
                    v = int(m.group(1), 16)
                    for pg in (pc & ~0xFFFF, 0, 0x10000):
                        s_ = cstr(rom, pg | v)
                        if s_:
                            note.append('"%s"' % s_); strs.setdefault(pg | v, set()).add(pc); break
                if i.kind in ("jmp", "ret", "ijmp"): pass
                f.write("  %05X: %-17s %-34s%s\n" % (pc, rom[pc:pc + i.len].hex(" "), txt, ("; " + ", ".join(note)) if note else ""))
                pc += i.len
            else:
                st = pc
                while pc < size and pc not in t.code and pc not in t.tables and pc - st < 16 and not (pc in names and pc != st):
                    pc += 1
                chunk = rom[st:pc]
                if chunk.count(0xFF) == len(chunk):
                    while pc < size and pc not in t.code and rom[pc] == 0xFF: pc += 1
                    f.write("  %05X: ; FF x %d\n" % (st, pc - st)); continue
                f.write("  %05X: db %-48s |%s|\n" % (st, chunk.hex(" "), "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)))
    with open(os.path.join(out, "ram.txt"), "w", encoding="utf-8") as f:
        for (dp, a), (rd, wr) in sorted(ram.items(), key=lambda x: ((x[0][0] if x[0][0] is not None else 9), x[0][1])):
            nm = ram_name(dp, a) or ""
            f.write("%s:%04X %-28s R%-3d W%-3d  w: %s\n" % ("?" if dp is None else dp, a, nm, len(rd), len(wr), " ".join("%05X" % x for x in sorted(wr)[:6])))
    with open(os.path.join(out, "tables.txt"), "w", encoding="utf-8") as f:
        for tb, (rows, caller, hs) in sorted(t.tables.items()):
            f.write("ui_table_%05X  rows %d  (JMP 0672 at %05X)\n" % (tb, rows, caller))
            for r in range(rows):
                f.write("   " + " ".join("%05X" % h for h in hs[13 * r:13 * r + 13]) + "\n")
    with open(os.path.join(out, "strings.txt"), "w", encoding="utf-8") as f:
        for a in sorted(strs):
            f.write("%05X %-36r <- %s\n" % (a, cstr(rom, a), " ".join("%05X" % x for x in sorted(strs[a]))))
    cov = len(set(b for pc, i in t.code.items() for b in range(pc, pc + i.len)))
    return cov, len(strs)


if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    t, vec = analyse(rom)
    cov, ns = write_listing(t, out)
    print("code %d B (%.1f%%), %d insns, %d subs, %d ui tables, %d string refs" %
          (cov, 100 * cov / len(rom), len(t.code), len(t.calls), len(t.tables), ns))
