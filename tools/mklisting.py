# Build a full traced listing of the HR-GP5 ROM: hrgp5.lst + stats.
import sys, os, struct, re
sys.path.insert(0, os.path.dirname(__file__))
from h8500dis import Dis, trace

rom = open(sys.argv[1], "rb").read()
out = sys.argv[2]
d = Dis(rom)
size = len(rom)

entries = set()
for v in range(0, 0x100, 4):
    a = struct.unpack(">I", rom[v:v + 4])[0]
    if a: entries.add(a)
extra = [int(x, 16) for x in sys.argv[3:]]
entries |= set(extra)

code, calls, labels = trace(d, sorted(entries), size)

# second pass: jump tables after JMP @(H'xxxx,Rn) - word tables in same page
for pc, i in list(code.items()):
    if i.kind in ("ijmp", "icall") and i.mem is not None:
        base = (pc & ~0xffff) | i.mem
        n = 0
        while n < 64 and base + 2 * n + 1 < size:
            t = (pc & ~0xffff) | struct.unpack(">H", rom[base + 2 * n:base + 2 * n + 2])[0]
            if t >= size or t < 0x180: break
            n += 1
        if n:
            ents = [(pc & ~0xffff) | struct.unpack(">H", rom[base + 2 * k:base + 2 * k + 2])[0] for k in range(n)]
            c2, k2, l2 = trace(d, ents, size)
            code.update(c2); calls |= k2; labels |= l2 | set(ents)

covered = bytearray(size)
for pc, i in code.items():
    for k in range(i.len): covered[pc + k] = 1

names = {a: ("sub_%05X" if a in calls else "loc_%05X") % a for a in labels}
xref = {}
for pc, i in code.items():
    if i.target is not None: xref.setdefault(i.target, []).append(pc)

with open(out, "w", encoding="utf-8") as f:
    pc = 0
    while pc < size:
        if pc in code:
            i = code[pc]
            if pc in names:
                xr = xref.get(pc, [])
                f.write("\n%s:%s\n" % (names[pc], ("   ; xref " + " ".join("%05X" % x for x in xr[:8]) + (" ..." if len(xr) > 8 else "")) if xr else ""))
            f.write("  %05X: %-17s %s\n" % (pc, rom[pc:pc + i.len].hex(" "), i.text))
            pc += i.len
        else:
            st = pc
            while pc < size and pc not in code and pc - st < 16 and not (pc in names and pc != st):
                pc += 1
            chunk = rom[st:pc]
            if chunk.count(0xff) == len(chunk):
                # collapse FF runs
                while pc < size and pc not in code and rom[pc] == 0xff: pc += 1
                f.write("  %05X: ; FF x %d\n" % (st, pc - st))
                continue
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            f.write("  %05X: db %-48s |%s|\n" % (st, chunk.hex(" "), asc))

print("insns %d, code bytes %d (%.1f%%), subs %d" % (len(code), sum(covered), 100 * sum(covered) / size, len(calls)))
for p in (0, 1):
    seg = covered[p * 0x10000:(p + 1) * 0x10000]
    print("page %d code %.1f%%" % (p, 100 * sum(seg) / len(seg)))
open(out + ".cov", "wb").write(bytes(covered))
