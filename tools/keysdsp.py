"""DDS-DSP SAMPLER program for the TMS57002 (HR-GP5 / HR-MP5): NV pitched voices playing one sample held in the delay
RAM, each layered with DDS oscillators (saw, pulse + width, triangle), a per-voice envelope, stereo pan, a stereo
resonant filter (Chamberlin SVF) and the input passed through (THRU, Sony's own so0 mix).

Units: dmem 24-bit Q23, cmem 32-bit Q31, macc Q47 (lmhd d -> d << 24; mac d,c -> c * d >> 7; mac c,a -> c * a >> 15).
Delay RAM (Sony's ST0 010820: 256K x 4 DRAM, 16-bit words) = 65536 samples, address = (xoa + xba) mod 65536,
xba counts down 1 per sample, so a fixed xoa moves with time: write at xoa 0 = "now".
  AGE   += 1.0 (16.16) per sample; the H8 zeroes it when a recording starts.
  voice: PH += DL (16.16, DL = pitch ratio * 65536, host); X = AGE - PH; reads at xoa = int(X) and int(X) + 1,
         interpolated with frac(X). The H8 sets PH = 0 on a note-on. Osc phase = PH << 10 (root = fs / 64,
         the same pitch as the boot sine), saw = that phase, pulse = sign(saw - PW), tri = 0.5 - |saw|.
  record: the slot at xoa 0 is read, then rewritten with old + REC * (src - old) (REC = 0: unchanged).
          src = G0 si0_l + G1 si0_r + G2 si1_l + G3 si1_r + GS sine. Boot: REC = 1, GS = 0.5 -> a 64-sample sine
          fills the RAM (1024 exact periods, so it loops seamlessly).
  env:   LVL += K * (T - LVL) (one-pole; the H8 switches T / K for attack, decay, release).
  out:   so0_l = WET lp_L + DRY sum_L + TH0 si0_l + TH1 si1_l (R alike), so1 = si1 (as Sony's MP5 program).
  lpc:   |sum_L| for Sony's meter read (its routine waits for host data: without an lpc the H8 hangs).

  python -I tools/keysdsp.py [--test] [--wav out/keys_test.wav]
"""
import os, sys, math, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tms57sim, tms57dis

NV = 4
ST0, ST1 = 0x010820, 0x000020           # Sony's I/O + memory config; ST1: MOVM (saturating outputs) only


# ---------------- ops ----------------
class Op:
    def __init__(s, txt, cat, c=None, d=None, a="", m="", port=None, ptr=None, post=False):
        s.txt, s.cat, s.c, s.d, s.a, s.m, s.port, s.ptr, s.post = txt, cat, c, d, a, m, port, ptr, post
    def __repr__(s): return s.txt


DIRECT_C = ("PH", "LVL", "SPH", "MET")     # state cells (read and written): direct address
def is_direct(x): return any(x == p or (x.startswith(p) and x[len(p):].isdigit()) for p in DIRECT_C)
def _c(x): return ("dir", x) if is_direct(x) else ("str", x)   # "str": c*+ stream slot, placed by the linker
def _d(x): return ("dir", x)
def C1(mn, c=None, d=None, a="", m="", port=None):
    return Op(mn, 1, c=_c(c) if c else None, d=_d(d) if d else None, a=a, m=m, port=port)
def lacc(c): return C1("lacc %c", c=c, a="w")
def lacd(d): return C1("lacd %d", d=d, a="w")
def addc(c): return C1("add %c,a", c=c, a="rw")
def subc(c): return C1("sub %c,a", c=c, a="rw")          # c - a
def subd(d): return C1("sub %d,a", d=d, a="rw")          # d - a
def addcm(c): return C1("add %c,m", c=c, a="w", m="mo")   # a = c + (mo >> 16)
def andc(c): return C1("and %c,a", c=c, a="rw")
def absa(): return C1("abs", a="rw")
def mpyca(c): return C1("mpy %c,a", c=c, a="r", m="w")
def macca(c): return C1("mac %c,a", c=c, a="r", m="rw")
def macsca(c): return C1("macs %c,a", c=c, a="r", m="rw")
def macad(d): return C1("mac a,%d", d=d, a="r", m="rw")
def mpydc(d, c): return C1("mpy %d,%c", d=d, c=c, m="w")
def macdc(d, c): return C1("mac %d,%c", d=d, c=c, m="rw")
def lmhd(d): return C1("lmhd %d", d=d, m="w")
def lmhc(c): return C1("lmhc %c", c=c, m="w")
def rde(c): return C1("rde %c", c=c, port="rd")
def wre(d, c): return C1("wre %d,%c", d=d, c=c, port="wr")
def C2(mn, c=None, d=None, a="", m="", port=None, post=False):
    return Op(mn, 2, c=_c(c) if c else None, d=_d(d) if d else None, a=a, m=m, port=port, post=post)
def sacc(c): return C2("sacc %c", c=c, a="r")
def sacd(d): return C2("sacd %d", d=d, a="r")
def smhd(d): return C2("smhd %d", d=d, m="mo")
def smhc(c): return C2("smhc %c", c=c, m="mo")
def slml(d): return C2("slml %d", d=d, m="mo")
def srbd(d): return C2("srbd %d", d=d, port="rb")
def dis(n, d): return C2("dis %s,%%d" % ("si0_l", "si0_r", "si1_l", "si1_r")[n], d=d)
def dos(n, d): return C2("dos %s,%%d" % ("so0_l", "so0_r", "so1_l", "so1_r")[n], d=d, post=True)
def domh(n): return C2("domh %s" % ("so0_l", "so0_r", "so1_l", "so1_r")[n], m="mo")
def lpc(c): return C2("lpc %c", c=c)
def rdat(c):
    """rde c*+ ; smhc c*+ in one word: store int(macc) into a fresh stream slot and start the RAM read from it."""
    return Op("rde %c ; smhc %c", 12, c=("str", c), m="mo", port="rd")


# ---------------- the program ----------------
def threads(nv=NV):
    T = {}
    T["in"] = ([], [dis(0, "IN0"), dis(1, "IN1"), dis(2, "IN2"), dis(3, "IN3")])
    T["age"] = ([], [lacd("AGE.r"), addc("ONE16"), andc("AMASK"), sacd("AGE.w")])     # AMASK = 0 resets
    T["sine"] = ([], [lacc("SPH"), addc("SINC"), sacc("SPH"), absa(), subc("ONEQ"),
                      mpyca("SPH"), macsca("SPH"), macca("SPH"), smhd("Y"),           # y = 4 x (1 - |x|)
                      lacd("Y"), absa(), mpyca("P225"), addcm("P775"),               # a = 0.775 + 0.225 |y|
                      lmhc("ZERO"), macad("Y"), smhd("SINE")])                        # sine = y * a
    T["rec"] = (["in", "sine"], [mpydc("IN0", "G0"), macdc("IN1", "G1"), macdc("IN2", "G2"),
                                 macdc("IN3", "G3"), macdc("SINE", "GS"), smhd("SRC"),
                                 rde("ZERO"), srbd("OLD"), lacd("OLD"), subd("SRC"), lmhd("OLD"),
                                 macca("REC"), smhd("W"), wre("W", "ZERO")])
    for v in range(nv):
        n = lambda s: "%s%d" % (s, v)
        T["v%d" % v] = (["age"], [
            lacc(n("PH")), addc(n("DL")), mpyca("K25"), sacc(n("PH")), subd("AGE.w"),  # a = X = AGE - PH
            slml(n("SAW")), mpyca("K15"), addc("ONE16"), rdat(n("A0")),               # read at int(X)
            mpyca("K15"), andc("M16"), srbd(n("S0")), rdat(n("A1")),                  # read at int(X) + 1
            mpyca("K30"), slml(n("FR")), lacd(n("S0")), srbd(n("S1")), subd(n("S1")),
            lmhd(n("S0")), macad(n("FR")), smhd(n("S")),                              # S = S0 + (S1 - S0) frac
            lacd(n("SAW")), absa(), subc("HALF"), sacd(n("TRI")),                     # tri = 0.5 - |saw|
            subc(n("PWT")), andc("SIGN"), addc("HALF"), sacd(n("PUL")),               # pulse = sign(PWT - tri) / 2
            mpydc(n("S"), n("SL")), macdc(n("SAW"), n("AL")), macdc(n("PUL"), n("PL")), macdc(n("TRI"), n("TL")),
            smhd(n("VM")),
            lacc(n("LVL")), subc(n("T")), mpyca(n("K")), addcm(n("LVL")), sacc(n("LVL")),   # LVL += K (T - LVL)
            lmhc("ZERO"), macad(n("VM")), smhd(n("VL"))])
    vs = ["v%d" % v for v in range(nv)]
    mix = []
    for ch, L in enumerate("LR"):
        for v in range(nv):
            mix.append((mpydc if v == 0 else macdc)("VL%d" % v, "PAN%s%d" % (L, v)))
        mix.append(smhd("SUM" + L))
    T["mix"] = (vs, mix)
    for ch, L in enumerate("LR"):
        T["out" + L] = (["mix", "in"], [
            lmhd("SUM" + L), macdc("LP%s.r" % L, "FM1" + L), macdc("BP%s.r" % L, "FMQ" + L),
            macdc("BP%s.r" % L, "FMF" + L), smhd("HP" + L),                            # hp = in - lp - q bp - f bp
            lmhd("BP%s.r" % L), macdc("HP" + L, "FF1" + L), smhd("BP%s.w" % L),
            lmhd("LP%s.r" % L), macdc("BP%s.r" % L, "FF2" + L), smhd("LP%s.w" % L),
            mpydc("LP%s.w" % L, "WET" + L), macdc("SUM" + L, "DRY" + L), macdc("IN%d" % ch, "TH0" + L),
            macdc("IN%d" % (ch + 2), "TH1" + L), domh(ch)] +
            ([addcm("ZERO"), absa(), sacc("MET"), lpc("MET")] if L == "L" else []))      # |out L| for Sony's meter read
    T["so1"] = (["in"], [dos(2, "IN2"), dos(3, "IN3")])
    return T


# ---------------- scheduler ----------------
def uses(o):
    r = set()
    if o.a: r.add("A")
    if o.m: r.add("M")
    if o.cat == 3: r.add(o.ptr[0])
    if o.c and o.c[0] == "ca": r.add("ca")
    if o.d and o.d[0] == "id": r.add("id")
    if o.port in ("rd", "rb"): r.add("P")
    return r


def chain_end(ops, j, res):
    """Last op index of the chain of resource res that op j belongs to (a fresh write / pointer load ends it)."""
    last = j
    for i in range(j + 1, len(ops)):
        o = ops[i]
        if res not in uses(o): continue
        if res == "A" and o.a == "w": break
        if res == "M" and o.m == "w": break
        if res in ("id", "ca") and o.cat == 3: break
        if res == "P":
            if o.port == "rb": return i
            break
        last = i
    return last


def closure(ops, i):
    ends = {r: chain_end(ops, i, r) for r in uses(ops[i])}
    changed = True
    while changed:
        changed = False
        for idx in range(i, max(ends.values()) + 1):
            for r in uses(ops[idx]):
                e = chain_end(ops, idx, r)
                if e > ends.get(r, -1): ends[r] = e; changed = True
    return ends


def schedule(T, prio=None):
    names = list(T)
    pos = {k: 0 for k in names}
    done = lambda k: pos[k] >= len(T[k][1])
    words = []
    owner = {"A": None, "M": None, "id": None, "ca": None, "P": None}
    end = {k: {} for k in names}
    Mlast = {}; port_free = 0; port_owner = None; rde_at = {}

    def op_ok(k, o, word, w):
        if T[k][0] and not all(done(x) for x in T[k][0]): return False
        cats = [x.cat for _, x in word]
        if o.cat in (3, 12): return not word and all(owner[r] in (None, k) for r in closure(T[k][1], pos[k])) and             (o.cat == 3 or (w >= port_free and owner["M"] == k and w >= Mlast.get(k, -9) + 2))
        if 3 in cats or 12 in cats: return False
        if o.cat in cats: return False
        mine = [x for j, x in word if j == k]
        if mine:
            e = mine[0]
            if not ((e.cat == 2 and not e.post and o.cat == 1) or (e.cat == 1 and o.cat == 2 and o.post)): return False
        for _, x in word:
            if o.c and x.c and o.c != x.c: return False
            if o.d and x.d and o.d != x.d: return False
        allc = o.c or next((x.c for _, x in word if x.c), None)
        alld = o.d or next((x.d for _, x in word if x.d), None)
        if allc and alld and allc[0] == "dir" and alld[0] == "dir": return False
        u = uses(o)
        if any(owner[r] not in (None, k) for r in u): return False
        fresh = [r for r in u if owner[r] != k]
        if fresh and any(owner[r] not in (None, k) for r in closure(T[k][1], pos[k])): return False
        if o.m == "mo" and (owner["M"] != k or w < Mlast.get(k, -9) + 2): return False
        if o.port in ("rd", "wr") and w < port_free: return False
        if o.port == "wr" and owner["P"] not in (None, k): return False
        if o.port == "rb" and (owner["P"] != k or w < rde_at[k] + 4): return False
        return True

    def commit(k, o, w):
        nonlocal port_free, port_owner
        i = pos[k]
        u = uses(o)
        if any(owner[r] != k for r in u):
            for r, e in closure(T[k][1], i).items():
                owner[r] = k; end[k][r] = max(e, end[k].get(r, -1) if owner[r] == k else -1)
        if o.m in ("w", "rw"): Mlast[k] = w
        if o.port in ("rd", "wr"):
            port_free = w + 4
            if o.port == "rd": rde_at[k] = w
        pos[k] += 1
        for r in owner:
            if owner[r] == k and pos[k] > end[k].get(r, -1): owner[r] = None

    w = 0
    while not all(done(k) for k in names):
        word = []
        progress = True
        while progress and not (word and word[0][1].cat in (3, 12)):
            progress = False
            for k in sorted([k for k in names if not done(k)], key=(lambda k: -(len(T[k][1]) - pos[k])) if prio is None else (lambda k: prio(k, pos[k], len(T[k][1])))):
                if done(k): continue
                o = T[k][1][pos[k]]
                if op_ok(k, o, word, w):
                    word.append((k, o)); commit(k, o, w); progress = True
                    if o.cat in (3, 12): break
        words.append(word)
        w += 1
        assert w < 400, "scheduler stuck at %s owners %s" % ({k: (pos[k], T[k][1][pos[k]] if not done(k) else None) for k in names}, owner)
    return words


# ---------------- link ----------------
def link(words, nv=NV):
    """c*+ stream slots get cmem addresses in program order (ca = 0 at every sample start); direct names after.
    Returns lines, cadr (direct name -> addr), cslot (addr -> base name), dadr."""
    cslot, cadr, dadr = {}, {}, {}
    n = 0
    for word in words:
        spec = next((o.c for k, o in word if o.c), None)
        if spec and spec[0] == "str": cslot[n] = spec[1]; n += 1
    for word in words:
        for k, o in word:
            if o.c and o.c[0] == "dir" and o.c[1] not in cadr: cadr[o.c[1]] = n; n += 1
    states = set(); dn = 0
    for word in words:
        for k, o in word:
            if o.d:
                nm = o.d[1]
                if nm.endswith(".r") or nm.endswith(".w"): states.add(nm[:-2])
                elif nm not in dadr: dadr[nm] = dn; dn += 1
    for sname in sorted(states):                   # state pairs: write at m, read next sample at m + 1
        dadr[sname + ".w"] = dn; dadr[sname + ".r"] = dn + 1; dn += 2
    assert n <= 256 and dn <= 256, (n, dn)
    lines = []
    slot = 0
    for word in words:
        spec = next((o.c for k, o in word if o.c), None)
        parts = []
        for k, o in word:
            t = o.txt
            if o.c: t = t.replace("%c", "c(%02x)" % cadr[o.c[1]] if o.c[0] == "dir" else "c*+")
            if o.d: t = t.replace("%d", "d(%02x)" % dadr[o.d[1]])
            parts.append((1 if o.cat == 12 else o.cat, t))
        if spec and spec[0] == "str": slot += 1
        c1 = [t for c, t in parts if c == 1]; c2 = [t for c, t in parts if c == 2]
        lines.append(" ; ".join(c1 + c2) if (c1 or c2) else "nop")
    lines.append("idle")
    assert len(lines) <= 256, "program too long: %d words" % len(lines)
    last_port = max(i for i, word in enumerate(words) if any(o.port in ("rd", "wr") for k, o in word))
    assert last_port <= len(lines) - 6, "delay-RAM access too close to the end (%d of %d)" % (last_port, len(lines))
    return lines, cadr, cslot, dadr


def q31(x): return max(-0x80000000, min(0x7FFFFFFF, int(round(x * 2147483648.0)))) & 0xFFFFFFFF


def init_values(nv=NV, platform="mp5"):
    v = dict(ONE16=0x10000, SINC=1 << 26, ONEQ=0x7FFFFFFF, P225=q31(0.225), P775=q31(0.775), ZERO=0,
             K15=0x8000, K25=1 << 25, K30=1 << 30, M16=0xFFFF, ONE=1, HALF=0x40000000, SIGN=0x80000000,
             REC=0x7FFFFFFF, G0=0, G1=0, G2=0, G3=0, GS=q31(0.5), AMASK=0xFFFFFFFF, SPH=0, MET=0)
    for vv in range(nv):
        v.update({"DL%d" % vv: 0x10000, "K%d" % vv: q31(0.001), "T%d" % vv: 0, "PWT%d" % vv: 0,
                  "SL%d" % vv: q31(1.0), "AL%d" % vv: 0, "PL%d" % vv: 0, "TL%d" % vv: 0,
                  "PANL%d" % vv: q31(0.5), "PANR%d" % vv: q31(0.5),
                  "PH%d" % vv: 0, "LVL%d" % vv: 0, "A0%d" % vv: 0, "A1%d" % vv: 0})
    th0, th1 = (0.5, 1.0) if platform == "mp5" else (0.32, 0.0)
    for L in "LR":
        v.update({"FM1" + L: 0x80000000, "FMQ" + L: q31(-1.0), "FMF" + L: q31(-0.5), "FF1" + L: q31(0.5), "FF2" + L: q31(0.5),
                  "WET" + L: 0, "DRY" + L: q31(1.0), "TH0" + L: q31(th0), "TH1" + L: q31(th1)})
    return v


def cmem_init(cadr, cslot, nv=NV, platform="mp5"):
    v = init_values(nv, platform)
    out = [0] * 256
    for n, a in cadr.items(): out[a] = v[n] & 0xFFFFFFFF
    for a, n in cslot.items(): out[a] = v[n] & 0xFFFFFFFF
    return out


def best_schedule(T, trials=400, seed=1):
    """Greedy list scheduling under a few hundred deterministic random thread priorities; keep the shortest."""
    import random
    rnd = random.Random(seed)
    best = schedule(T)
    for trial in range(trials):
        wts = {k: rnd.random() for k in T}
        mode = trial % 3
        if mode == 0: pr = lambda k, p, n, wts=wts: -(n - p) * wts[k]
        elif mode == 1: pr = lambda k, p, n, wts=wts: wts[k]
        else: pr = lambda k, p, n, wts=wts: -(n - p) + 10 * wts[k]
        try: W = schedule(T, pr)
        except AssertionError: continue
        if len(W) < len(best): best = W
    return best


class Image:
    def __init__(s, nv=NV, platform="mp5"):
        T = threads(nv)
        s.words = best_schedule(T)
        s.lines, s.cadr, s.cslot, s.dadr = link(s.words, nv)
        s.prog = tms57sim.assemble(s.lines)
        for l, w in zip(s.lines, s.prog):
            assert tms57dis.dis(w) == l, (l, tms57dis.dis(w))
        s.cmem = cmem_init(s.cadr, s.cslot, nv, platform)
        s.nv = nv

    def addrs(s, name):
        """Every cmem address holding coefficient `name` (stream copies + direct)."""
        r = [a for a, n in s.cslot.items() if n == name]
        if name in s.cadr: r.append(s.cadr[name])
        assert r, name
        return sorted(r)


def build(nv=NV, platform="mp5"):
    return Image(nv, platform)


# ---------------- the H8 side: generated assembler (src/keys.asm includes it through tools/rosetta_build.py) ----------------
FS = 44100.0


def gen_asm(im, platform="mp5"):
    p, c = image_bytes(im.prog, im.cmem)
    out = ["; generated by tools/keysdsp.py: DDS-DSP SAMPLER program image, coefficient addresses, value tables",
           "        .equ KEYS_PROG_LEN, %d" % len(p), "        .equ KEYS_CMEM_LEN, %d" % len(c)]
    def one(name):
        a = im.addrs(name); assert len(a) == 1, (name, a); return a[0]
    for name in ("GS", "REC", "AMASK"): out.append("        .equ KA_%s, %d" % (name, one(name)))
    lists = {"DL": ["DL%d" % v for v in range(im.nv)], "PH": ["PH%d" % v for v in range(im.nv)],
             "T": ["T%d" % v for v in range(im.nv)], "K": ["K%d" % v for v in range(im.nv)],
             "SL": ["SL%d" % v for v in range(im.nv)], "AL": ["AL%d" % v for v in range(im.nv)],
             "PL": ["PL%d" % v for v in range(im.nv)], "TL": ["TL%d" % v for v in range(im.nv)],
             "PWT": ["PWT%d" % v for v in range(im.nv)], "PANL": ["PANL%d" % v for v in range(im.nv)],
             "PANR": ["PANR%d" % v for v in range(im.nv)], "FF": ["FF1L", "FF2L", "FF1R", "FF2R"],
             "FMF": ["FMFL", "FMFR"], "FMQ": ["FMQL", "FMQR"], "WET": ["WETL", "WETR"], "DRY": ["DRYL", "DRYR"],
             "TH0": ["TH0L", "TH0R"], "TH1": ["TH1L", "TH1R"], "G": ["G0", "G1", "G2", "G3"]}
    for k, names in lists.items():
        out.append("KA_%s: .db %s" % (k, ", ".join(str(one(n)) for n in names)))
    out.append("        .align 2")
    def t32(label, vals):
        out.append(label + ":")
        for i in range(0, len(vals), 4):
            out.append("        .dw     " + ", ".join("0x%04X, 0x%04X" % (v >> 16 & 0xFFFF, v & 0xFFFF) for v in vals[i:i + 4]))
    t32("kt_lvl", [q31((v / 99.0) ** 2) for v in range(100)])
    t32("kt_lin", [q31(v / 99.0) for v in range(100)])
    taus = [0.001 * 10 ** (4.0 * v / 99) for v in range(100)]
    t32("kt_rate", [q31(1 - math.exp(-1.0 / (t * FS))) for t in taus])
    out.append("kt_atk:")
    atk = [max(1, int(round(3 * t / 0.005))) for t in taus]
    for i in range(0, 100, 10): out.append("        .dw     " + ", ".join(str(v) for v in atk[i:i + 10]))
    t32("kt_cut", [q31(2 * math.sin(math.pi * min(30 * (7000 / 30.0) ** (v / 98.0), 7000) / FS)) for v in range(100)])
    t32("kt_res", [q31(min(0.99999, (0.08 / 1.0) ** (v / 99.0))) for v in range(100)])
    t32("kt_note", [int(round(65536 * 2 ** ((i - 64) / 12.0))) for i in range(128)])
    out.append("keys_prog:")
    for i in range(0, len(p), 16): out.append("        .db     " + ", ".join("0x%02X" % b for b in p[i:i + 16]))
    out.append("keys_cmem:")
    for i in range(0, len(c), 16): out.append("        .db     " + ", ".join("0x%02X" % b for b in c[i:i + 16]))
    return "\n".join(out) + "\n"


# ---------------- host helpers (what the H8 sends), used by the tests ----------------
def note_delta(semis): return int(round(65536 * 2 ** (semis / 12.0)))


def image_bytes(prog, cmem):
    """PLOAD stream (ST0, ST1, 256 x 3 bytes) and CLOAD+PLOAD stream (256 x 4 bytes), as Sony's 0x1468B / 0x14678 send."""
    p = bytearray(ST0.to_bytes(3, "big") + ST1.to_bytes(3, "big"))
    for w in prog + [0] * (256 - len(prog)): p += w.to_bytes(3, "big")
    c = bytearray()
    for v in cmem: c += v.to_bytes(4, "big")
    return bytes(p), bytes(c)


def selftest(wav=None):
    im = build()
    print("program: %d words (incl. idle), %d cmem words, %d dmem words" % (len(im.lines), len(im.cadr) + len(im.cslot), max(im.dadr.values()) + 1))
    t = tms57sim.TMS()
    t.load(ST0, ST1, im.prog, im.cmem)
    sg = lambda v: v - (1 << 24) if v & 0x800000 else v
    out = []
    def run(n, si=(0, 0, 0, 0)):
        for _ in range(n): out.append(t.sample(si))
    def upd(name, val):
        for a in im.addrs(name): t.host_update(a, val); run(2)
    def freq(y):
        z = [i for i in range(1, len(y)) if y[i - 1] < 0 <= y[i]]
        return (len(z) - 1) / ((z[-1] - z[0]) / 44100.0) if len(z) > 2 else 0
    def rms(y): return math.sqrt(sum(v * v for v in y) / len(y)) / 0x800000
    def tail(n, ch=0): return [sg(o[ch]) for o in out[-n:]]
    ok = True
    def check(name, cond, info):
        nonlocal ok
        print("%-28s %s  %s" % (name, "OK " if cond else "BAD", info)); ok = ok and cond
    run(66000)                                      # boot: the sine fills the RAM
    check("silent at boot", rms(tail(2000)) < 1e-4, "rms %.5f" % rms(tail(2000)))
    upd("PH0", 0); upd("K0", q31(0.01)); upd("T0", q31(0.9)); run(8000)
    f1, r1 = freq(tail(4000)), rms(tail(4000))
    check("note at root", abs(f1 - 44100 / 64) < 2, "%.1f Hz (expect %.1f), rms %.3f" % (f1, 44100 / 64, r1))
    check("level", 0.10 < r1 < 0.20, "expect 0.5 sine * 0.9 * pan 0.5 -> %.3f" % (0.5 * 0.9 * 0.5 / math.sqrt(2)))
    upd("DL0", note_delta(12)); run(8000)
    f2 = freq(tail(4000))
    check("+12 semitones", abs(f2 - 44100 / 32) < 3, "%.1f Hz (expect %.1f)" % (f2, 44100 / 32))
    upd("DL0", note_delta(7)); run(8000)
    f3 = freq(tail(4000))
    check("+7 semitones", abs(f3 - 44100 / 64 * 2 ** (7 / 12)) < 3, "%.1f Hz (expect %.1f)" % (f3, 44100 / 64 * 2 ** (7 / 12)))
    l, r = tail(4000, 0), tail(4000, 1)
    check("stereo L = R (pan centre)", max(abs(a - b) for a, b in zip(l, r)) < 64, "max diff %d" % max(abs(a - b) for a, b in zip(l, r)))
    upd("K0", q31(0.0005)); upd("T0", 0); run(30000)
    check("release to silence", rms(tail(2000)) < 1e-3, "rms %.5f" % rms(tail(2000)))
    upd("SL0", 0); upd("AL0", q31(0.8)); upd("DL0", note_delta(0)); upd("K0", q31(0.01)); upd("T0", q31(0.9)); run(6000)
    f4, r4 = freq(tail(3000)), rms(tail(3000))
    check("saw layer", abs(f4 - 44100 / 64) < 2 and 0.18 < r4 < 0.24, "%.1f Hz, rms %.3f (expect ~%.3f)" % (f4, r4, 0.8 * 0.9 * 0.5 / math.sqrt(3)))
    upd("AL0", 0); upd("PL0", q31(0.8)); run(4000)
    r5 = rms(tail(2000))
    check("pulse layer (50%)", 0.15 < r5 < 0.21, "rms %.3f (expect ~%.3f)" % (r5, 0.8 * 0.9 * 0.5 * 0.5))
    upd("PL0", 0); upd("TL0", q31(0.8)); run(4000)
    r6 = rms(tail(2000))
    check("triangle layer", 0.08 < r6 < 0.12, "rms %.3f (expect ~%.3f)" % (r6, 0.8 * 0.9 * 0.5 * 0.5 / math.sqrt(3)))
    # filter: cutoff low, WET 1 DRY 0 on the saw -> much lower rms
    upd("TL0", 0); upd("AL0", q31(0.8))
    f = 2 * math.sin(math.pi * 300 / 44100); q = 1.0
    for L in "LR":
        upd("FF1" + L, q31(f)); upd("FF2" + L, q31(f)); upd("FMF" + L, q31(-f)); upd("FMQ" + L, q31(-q))
        upd("WET" + L, q31(1.0)); upd("DRY" + L, 0)
    run(6000)
    r7 = rms(tail(3000))
    check("low-pass 300 Hz on the saw", r7 < r4 * 0.6, "rms %.3f (was %.3f)" % (r7, r4))
    # voice 1 + 2 together (poly)
    for L in "LR": upd("WET" + L, 0); upd("DRY" + L, q31(1.0))
    upd("AL0", 0); upd("SL0", q31(1.0))
    for v, semi in ((1, 4), (2, 7)):
        upd("SL%d" % v, q31(1.0)); upd("DL%d" % v, note_delta(semi)); upd("PH%d" % v, 0); upd("K%d" % v, q31(0.01)); upd("T%d" % v, q31(0.9))
    run(8000)
    y = tail(8192)
    def tone(fr): return 2 * math.hypot(sum(v * math.cos(2 * math.pi * fr * i / 44100) for i, v in enumerate(y)),
                                        sum(v * math.sin(2 * math.pi * fr * i / 44100) for i, v in enumerate(y))) / len(y) / 0x800000
    root = 44100 / 64
    amps = [tone(root * 2 ** (s_ / 12)) for s_ in (0, 4, 7)]
    check("3-voice chord", all(a > 0.1 for a in amps), "root %.3f, +4 %.3f, +7 %.3f" % tuple(amps))
    # thru: a 220 Hz input on si1, voices off
    for v in range(3): upd("T%d" % v, 0)
    run(30000)
    for n in range(3000):
        x = int(0x200000 * math.sin(2 * math.pi * 220 * n / 44100)) & 0xFFFFFF
        out.append(t.sample((0, 0, x, x)))
    r8 = rms(tail(2000))
    check("thru si1 -> so0", abs(r8 - 0x200000 / math.sqrt(2) / 0x800000) < 0.01, "rms %.3f (in %.3f)" % (r8, 0x200000 / math.sqrt(2) / 0x800000))
    check("so1 = si1 copy", tail(1, 2)[0] == sg(x), "so1_l %d si1_l %d" % (tail(1, 2)[0], sg(x)))
    check("lpc armed (Sony meter read)", t.host is not None, "host word %s" % (None if t.host is None else "%08X" % t.host))
    print("instructions per sample %.0f" % (t.icount / len(out)))
    if wav:
        os.makedirs(os.path.dirname(os.path.abspath(wav)), exist_ok=True)
        with open(wav, "wb") as f:
            n = len(out)
            f.write(b"RIFF" + struct.pack("<I", 36 + n * 4) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 4, 4, 16))
            f.write(b"data" + struct.pack("<I", n * 4))
            for o in out: f.write(struct.pack("<hh", sg(o[0]) >> 8, sg(o[1]) >> 8))
        print("wrote", wav)
    print("ALL OK" if ok else "FAILURES")
    return ok


if __name__ == "__main__":
    if "--list" in sys.argv:
        im = build()
        for i, l in enumerate(im.lines): print("%3d  %06X  %s" % (i, im.prog[i], l))
    if "--test" in sys.argv:
        w = sys.argv[sys.argv.index("--wav") + 1] if "--wav" in sys.argv else None
        selftest(w)
