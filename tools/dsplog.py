# Decode the DSP host-bus traffic logged by gp5emu (FD00 / FD10 / FD11 writes).
#   FD10 = 4: TMS57002. Each byte goes out as FD11 = E0 (clock/marker) then FD11 = ~data.
#             FD00 bit 1 = /PLOAD, bit 0 = /CLOAD (low = active), like MAME's tms57002 pload/cload lines:
#               PLOAD:          3-byte words: ST0, ST1, then program words from pc 0
#               PLOAD + CLOAD:  4-byte words straight into coefficient memory (cmem) from 0
#               CLOAD:          1 address byte + 4-byte value = coefficient update (cvar)
#   FD10 = 7: the second DSP (Sony custom). FD00 bit 4 low = address bytes, high = data bytes.
#   python -I tools/dsplog.py ROM [--secs 1.0] [--keys ...]
import sys, os, pickle, collections
sys.path.insert(0, os.path.dirname(__file__))


class Tms:
    def __init__(s):
        s.st0 = s.st1 = None; s.prog = []; s.cmem = {}; s.updates = []; s.loads = 0
        s.mode = "run"; s.buf = bytearray(); s.ca = 0; s.sa = None; s.pwords = 0

    def set_mode(s, fd00):
        pl, cl = not (fd00 >> 1) & 1, not fd00 & 1
        m = "cmem" if pl and cl else "pload" if pl else "cval" if cl else "run"
        if m != s.mode:
            if m == "pload": s.pwords = 0; s.prog = []; s.loads += 1
            if m == "cmem": s.ca = 0
            s.buf = bytearray(); s.sa = None
            s.mode = m

    def byte(s, d):
        s.buf.append(d)
        if s.mode == "pload" and len(s.buf) == 3:
            w = int.from_bytes(s.buf, "big"); s.buf = bytearray()
            if s.pwords == 0: s.st0 = w
            elif s.pwords == 1: s.st1 = w
            else: s.prog.append(w)
            s.pwords += 1
        elif s.mode == "cmem" and len(s.buf) == 4:
            s.cmem[s.ca] = int.from_bytes(s.buf, "big"); s.ca += 1; s.buf = bytearray()
        elif s.mode == "cval":
            if s.sa is None: s.sa = d; s.buf = bytearray()
            elif len(s.buf) == 4:
                s.updates.append((s.sa, int.from_bytes(s.buf, "big"))); s.sa = None; s.buf = bytearray()


def decode(log):
    tms = Tms()
    dsp2 = []                       # (kind, bytes): kind 'a' address, 'd' data
    fd00, fd10, pend_e0 = 0xFF, 0, False
    for o, v, t in log:
        if o == 0xFD00:
            fd00 = v
            if fd10 == 4: tms.set_mode(fd00)
            continue
        if o == 0xFD10: fd10 = v; continue
        if fd10 == 4:
            if v == 0xE0 and not pend_e0: pend_e0 = True; continue
            pend_e0 = False
            tms.byte((~v) & 0xFF)
        elif fd10 == 7:
            k = "d" if fd00 & 0x10 else "a"
            if not dsp2 or dsp2[-1][0] != k: dsp2.append([k, bytearray()])
            dsp2[-1][1].append(v)
    return tms, dsp2


if __name__ == "__main__":
    import argparse, gp5emu, tms57dis as T
    ap = argparse.ArgumentParser()
    ap.add_argument("rom"); ap.add_argument("--secs", type=float, default=0.9); ap.add_argument("--keys", default="")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "out", "dsp"))
    a = ap.parse_args()
    e = gp5emu.Emu(open(a.rom, "rb").read()); e.dsplog = []
    e.run(int(a.secs * 1_500_000))
    for k in [k for k in a.keys.split(",") if k]: e.press(k)
    tms, dsp2 = decode(e.dsplog)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "tms57002_program.txt"), "w", encoding="utf-8") as f:
        f.write("ST0 %06X  ST1 %06X  program loads %d  words %d\n" % (tms.st0 or 0, tms.st1 or 0, tms.loads, len(tms.prog)))
        for i, w in enumerate(tms.prog): f.write("%3d  %06X  %s\n" % (i, w, T.dis(w)))
        f.write("\ncmem (%d words loaded):\n" % len(tms.cmem))
        for k in sorted(tms.cmem): f.write("  c(%02x) = %08X\n" % (k, tms.cmem[k]))
        cnt = collections.Counter(sa for sa, v in tms.updates)
        f.write("\ncoefficient updates: %d (addresses: %s)\n" % (len(tms.updates), " ".join("%02x:%d" % kv for kv in sorted(cnt.items()))))
    with open(os.path.join(a.out, "dsp2_traffic.txt"), "w", encoding="utf-8") as f:
        for k, b in dsp2: f.write("%s %4d  %s\n" % (k, len(b), b[:48].hex(" ")))
    print("TMS57002: ST0 %06X ST1 %06X, %d program words (%d loads), cmem %d words, %d coefficient updates"
          % (tms.st0 or 0, tms.st1 or 0, len(tms.prog), tms.loads, len(tms.cmem), len(tms.updates)))
    print("DSP2: %d address writes, %d data bytes" % (sum(1 for k, b in dsp2 if k == "a"), sum(len(b) for k, b in dsp2 if k == "d")))
