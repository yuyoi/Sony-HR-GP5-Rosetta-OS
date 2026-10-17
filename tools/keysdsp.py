"""DDS-DSP SAMPLER for the TMS57002 (HR-MP5): 8 voices playing the DSP's delay RAM, which holds either a wavetable
(SYNTH: one DDS oscillator, sine / saw / pulse + width / triangle, recorded continuously as a 64-sample cycle) or a
recording of the input (SAMPLE). Per voice: pitch, interpolation, one-pole envelope. Then L / R groups (even / odd
voices) with crossfeed (SPREAD), a stereo resonant low-pass (Chamberlin SVF), THRU (Sony's own so0 mix), so1 = si1.

Units: dmem 24-bit Q23, cmem 32-bit Q31, macc Q47 (lmhd d -> d << 24; mac d,c -> c * d >> 7; mac c,a -> c * a >> 15).
Delay RAM (Sony's ST0 010820: 256K x 4 DRAM, 16-bit words) = 65536 samples, address = (xoa + xba) mod 65536,
xba counts down 1 per sample, so a fixed xoa moves with time: xoa 0 = "now".
  AGE    += 1.0 (16.16) per sample; AMASK = 0 for a moment zeroes it (start of a recording).
  osc:   OPH += 2^26 (period 64 = fs / 64, 689 Hz at 44.1 kHz); saw = OPH, tri = 0.5 - |saw| (for the pulse),
         sine = 3 saw (1 - |saw|) (a parabola, peak 0.75), pulse = sign(PWT - tri) / 2 (PWT 0 = square).
  record: the slot at xoa 0 is rewritten every sample with W = OLD + RG0 si0_l + RG2 si1_l + RGS sine + RGW saw +
         RGP pulse + MREC OLD (the H8 sends RGx = level * REC, MREC = -REC; all 0 = the slot stays).
         SYNTH keeps writing the wave mix, so the 64-sample cycle repeats through the whole RAM.
  voice: PH += DL (16.16, pitch ratio); X = (AGE - PH) & XM; reads at int(X) and int(X) + 1, interpolated.
         SAMPLE: XM = all ones, X = position since the recording start (the H8 sets PH = 0 on a note-on).
         SYNTH:  XM = 64 samples - 1, so the voice reads the last 64 samples: wave edits are heard at once.
         LVL += K (T - LVL): the H8 switches T / K for attack, decay, release.
  sums:  SUML = sum LVL_v SV_v over even voices, SUMR over odd ones (LVL is the coefficient).
  out:   filter in = VIN SUML + VXF SUMR (crossfeed), so0_l = WET lp + DRY SUML + DRYX SUMR + TH0 si0_l + TH1 si1_l.
  lpc:   |out L| for Sony's meter read (its routine waits for host data).

  python -I tools/keysdsp.py --test [--wav out/keys/keys_selftest.wav]     python -I tools/keysdsp.py --list
"""
import os, sys, math, struct, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tms57sim, tms57dis

NV = 8
ST0, ST1 = 0x010820, 0x000020           # Sony's I/O + memory config; ST1: MOVM (saturating outputs) only
XM_SYNTH = (64 << 16) - 1
FS = 44100.0


# ---------------- ops ----------------
class Op:
    def __init__(s, txt, cat, c=None, d=None, a="", m="", port=None, ptr=None, post=False):
        s.txt, s.cat, s.c, s.d, s.a, s.m, s.port, s.ptr, s.post = txt, cat, c, d, a, m, port, ptr, post
        s.tag = s.ref = None
        s.after = ()
    def __repr__(s): return s.txt


def tag(o, t): o.tag = t; return o            # name an M write
def ref(o, t): o.ref = t; return o            # an M read of that named write (default: the thread's latest M write)
def after(o, *thr): o.after = thr; return o   # this op waits until those threads are done


DIRECT_C = ("PH", "LVL", "OPH")              # state cells (read and written): direct address
def is_direct(x): return any(x == p or (x.startswith(p) and x[len(p):].isdigit()) for p in DIRECT_C)
def _c(x): return ("dir", x) if is_direct(x) else ("str", x)   # "str": a c*+ stream slot, placed by the linker
def _d(x): return x if isinstance(x, tuple) else ("dir", x)
def ptr(name, inc=False): return ("id", inc, name)             # d via the id pointer (expected cell, checked at link)
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
def slml(d): return C2("slml %d", d=d, m="mo")
def srbd(d): return C2("srbd %d", d=d, port="rb")
def dis(n, d): return C2("dis %s,%%d" % ("si0_l", "si0_r", "si1_l", "si1_r")[n], d=d)
def dos(n, d): return C2("dos %s,%%d" % ("so0_l", "so0_r", "so1_l", "so1_r")[n], d=d, post=True)
def domh(n): return C2("domh %s" % ("so0_l", "so0_r", "so1_l", "so1_r")[n], m="mo")
def lpc(c): return C2("lpc %c", c=c)
def rdat(c):
    """rde c*+ ; smhc c*+ in one word: store int(macc) into a fresh stream slot and start the RAM read from it."""
    return Op("rde %c ; smhc %c", 12, c=("str", c), m="mo", port="rd")
def lirk(name): return Op("lirk %i", 3, ptr=name)


# ---------------- the program ----------------
SBLK = lambda nv: ["SV%d" % v for v in range(0, nv, 2)] + ["SV%d" % v for v in range(1, nv, 2)]


def threads(nv=NV):
    T = {}
    T["in"] = ([], [dis(0, "IN0"), dis(1, "IN1"), dis(2, "IN2"), dis(3, "IN3")])
    T["age"] = ([], [lacd("AGE.r"), addc("ONE16"), andc("AMASK"), sacd("AGE.w"), after(lirk("AGE.w"), "sumrd")])
    T["osc"] = ([], [lacc("OPH"), addc("OINC"), sacc("OPH"), sacd("SAW"), absa(), subc("HALF"), sacd("TRI"),
                     addc("HALF"), mpyca("OPH"), macsca("OPH"), smhd("SINE"),                      # 3 x (1 - |x|)
                     lacd("TRI"), subc("PWT"), andc("SIGN"), addc("HALF"), sacd("PUL")])            # sign(PWT - tri) / 2
    T["rec"] = (["in", "osc"], [rde("ZERO"), srbd("OLD"), lmhd("OLD"), macdc("IN0", "RG0"), macdc("IN2", "RG2"),
                                macdc("SINE", "RGS"), macdc("SAW", "RGW"), macdc("PUL", "RGP"),
                                macdc("OLD", "MREC"), smhd("W"), wre("W", "ZERO")])
    # last sample's voice outputs (SVx.r) -> SUML (even voices) / SUMR (odd), LVL as the coefficient
    blk = SBLK(nv)
    s = [lirk(blk[0] + ".r")]
    for i, nm in enumerate(blk):
        s.append((mpydc if i in (0, nv // 2) else macdc)(ptr(nm + ".r", True), "LVL%s" % nm[2:]))
        if i in (nv // 2 - 1, nv - 1): s.append(smhd("SUML" if i < nv // 2 else "SUMR"))
    T["sumrd"] = ([], s)                                   # then the age thread points id at AGE for the voices
    for v in range(nv):
        n = lambda x: "%s%d" % (x, v)
        T["v%d" % v] = (["age"], [
            lacc(n("PH")), addc(n("DL")), sacc(n("PH")), subd(ptr("AGE.w")), andc(n("XM")),   # a = X
            tag(mpyca("K15"), "X"), addc("ONE16"), tag(mpyca("K15"), "X1"), ref(rdat(n("A0")), "X"),
            andc("M16"), tag(mpyca("K30"), "F"), ref(rdat(n("A1")), "X1"), ref(slml(n("FR")), "F"),
            srbd(n("XA")), lacd(n("XA")), srbd(n("XB")), subd(n("XB")),
            after(lmhd(n("XA")), "sumrd"), macad(n("FR")), smhd(n("SV") + ".w"),             # XA + (XB - XA) frac
            lacc(n("KD")), mpyca(n("LVL")), addcm(n("KT")), sacc(n("LVL"))])                # LVL = KD LVL + KT
    for ch, (L, O) in enumerate((("L", "R"), ("R", "L"))):
        T["out" + L] = (["sumrd", "in"], [
            mpydc("SUM" + L, "VIN" + L), macdc("SUM" + O, "VXF" + L),
            macdc("LP%s.r" % L, "FM1" + L), macdc("BP%s.r" % L, "FMQ" + L), macdc("BP%s.r" % L, "FMF" + L),
            smhd("HP" + L),                                                                 # hp = in - lp - (q + f) bp
            lmhd("BP%s.r" % L), macdc("HP" + L, "FF1" + L), smhd("BP%s.w" % L),            # bp += f hp
            lmhd("LP%s.r" % L), tag(macdc("BP%s.r" % L, "FF2" + L), "LP"),                 # lp += f bp
            macdc("IN%d" % ch, "TH0" + L), ref(smhd("LP%s.w" % L), "LP"),                   # + THRU, lp stored on the way
            macdc("IN%d" % (ch + 2), "TH1" + L), domh(ch)])
    T["so1"] = (["in"], [dos(2, "IN2"), dos(3, "IN3")])
    return T


# ---------------- scheduler ----------------
# Exact TMS57002 timing: a mo / mv read at word t sees the macc of word t - 2 (the last M write at or before t - 2),
# so a read may come right after the next write. Ownership: A for a value's live range, M for an accumulate chain,
# P (delay-RAM port) from a read to its srbd, I (id pointer) while walking. A thread acquiring a resource also takes
# the lower-order ones its chain needs (order A < P < I < M): holders never wait for a lower resource -> no deadlock.
ORDER = {"A": 0, "P": 1, "I": 2, "M": 3}


def uses(o):
    r = set()
    if o.a: r.add("A")
    if o.m in ("w", "rw"): r.add("M")
    if o.port in ("rd", "rb"): r.add("P")
    if o.cat == 3 or (o.d and o.d[0] == "id" and o.d[1]): r.add("I")
    return r


_rcache = {}
def mreaders(ops):
    """M write index -> indices of the mo reads of it (tag / ref, default the latest write before the read)"""
    key = id(ops)
    if key not in _rcache:
        rd, tags, lastw = {}, {}, None
        for i, o in enumerate(ops):
            if o.m in ("w", "rw"):
                lastw = i
                if o.tag: tags[o.tag] = i
            if o.m == "mo": rd.setdefault(tags[o.ref] if o.ref else lastw, []).append(i)
        _rcache[key] = (ops, rd)
    return _rcache[key][1]


def chain_end(ops, j, res):
    last = j
    if res == "M":                       # the writes of the chain (w, then rw) and every read of them
        wr = [j]
        for i in range(j + 1, len(ops)):
            if ops[i].m == "w": break
            if ops[i].m == "rw": wr.append(i)
        rd = mreaders(ops)
        return max([wr[-1]] + [r for x in wr for r in rd.get(x, [])])
    if res == "P":
        cnt = 0
        for i in range(j, len(ops)):
            p = ops[i].port
            if p == "rd": cnt += 1
            elif p == "rb":
                cnt -= 1
                if cnt <= 0: return i
        return len(ops) - 1
    for i in range(j + 1, len(ops)):
        o = ops[i]
        if res not in uses(o): continue
        if res == "A" and o.a == "w": break
        if res == "M" and o.m == "w": break
        if res == "I" and o.cat == 3: break
        last = i
    return last


def need_for(ops, i, r):
    e = chain_end(ops, i, r)
    need = {r: e}
    for idx in range(i, e + 1):
        for s in uses(ops[idx]):
            if ORDER[s] < ORDER[r] and s not in need:
                need[s] = max(e, chain_end(ops, idx, s))
    return need


class Stuck(Exception):
    words = None


def schedule(T, prio=None, max_empty=12, blocks=None):
    names = list(T)
    ops = {k: T[k][1] for k in names}
    pos = {k: 0 for k in names}
    done = lambda k: pos[k] >= len(ops[k])
    # M references: reader op -> written op (same thread)
    refidx = {}
    readers = {}
    for k in names:
        tags, lastw = {}, None
        for i, o in enumerate(ops[k]):
            if o.m in ("w", "rw"):
                lastw = i
                if o.tag: tags[o.tag] = i
            if o.m == "mo":
                j = tags[o.ref] if o.ref else lastw
                assert j is not None, (k, i, o)
                refidx[(k, i)] = j
                readers.setdefault((k, j), []).append(i)
    nextcell = {}
    for blk in (blocks or []):
        for a, b in zip(blk, blk[1:]): nextcell[a] = b
    words = []
    owner = {"A": None, "P": None, "I": None, "M": None}
    end = {k: {} for k in names}
    mw = {}                                   # word -> (thread, op) M write
    placed = {}
    port_free = 0
    preads = {k: [] for k in names}          # outstanding delay-RAM reads (issue words)
    id_val = None
    forced = {}

    def last_mw(t):
        best = None
        for wd in mw:
            if wd <= t and (best is None or wd > best): best = wd
        return mw[best] if best is not None else None

    def timing_ok(k, i, o, w):
        if o.m == "mo":
            j = refidx[(k, i)]
            p = placed.get((k, j))
            if p is None or w < p + 2 or last_mw(w - 2) != (k, j): return False
        if o.port in ("rd", "wr") and w < port_free: return False
        if o.port == "rb":
            if not preads[k] or w < preads[k][0] + 4: return False
            if len(preads[k]) > 1 and w >= preads[k][1] + 4: return False
        return True

    def op_ok(k, o, word, w):
        i = pos[k]
        if T[k][0] and not all(done(x) for x in T[k][0]): return False
        if o.after and not all(done(x) for x in o.after): return False
        cats = [x.cat for _, x in word]
        if o.cat in (3, 12) and word: return False
        if 3 in cats or 12 in cats: return False
        if o.cat in (1, 2) and o.cat in cats: return False
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
        for r in u:
            if owner[r] != k and any(owner[s] not in (None, k) for s in need_for(ops[k], i, r)): return False
        if o.d and o.d[0] == "id":
            if id_val != o.d[2]: return False
            if not o.d[1] and owner["I"] is not None: return False
        if o.cat == 3 and owner["I"] not in (None, k): return False
        if not timing_ok(k, i, o, w): return False
        if o.port in ("rd", "wr") and any(ops[k2][i2].port == "rd" for k2, i2 in forced.get(w + 1, [])): return False
        if o.m in ("w", "rw"):                       # every pending read must still fit at w or w + 1
            for (k2, j2), rs in readers.items():
                if (k2, j2) not in placed: continue
                for i2 in rs:
                    if (k2, i2) in placed: continue
                    if any(j == k2 and pos[k2] > i2 for j, _ in word): continue
                    nxt = i + 1 if k2 == k else pos[k2]
                    if i2 != nxt: return False
                    if not timing_ok(k2, i2, ops[k2][i2], w + 1): return False
                    for r in uses(ops[k2][i2]):
                        if any(owner[x] not in (None, k2) for x in need_for(ops[k2], i2, r)): return False
                    if uses(ops[k2][i2]) & uses(o) and k2 != k: return False
        return True

    def commit(k, o, w):
        nonlocal port_free, id_val
        i = pos[k]
        for r in uses(o):
            if owner[r] != k:
                for s, e in need_for(ops[k], i, r).items():
                    if owner[s] != k: owner[s] = k; end[k][s] = e
                    else: end[k][s] = max(end[k][s], e)
            else:                                    # a new chain inside a held span: hold it to its end too
                for s, e in need_for(ops[k], i, r).items():
                    if owner[s] in (None, k): owner[s] = k; end[k][s] = max(end[k].get(s, -1), e)
        placed[(k, i)] = w
        if o.m in ("w", "rw"):
            mw[w] = (k, i)
            for (k2, j2), rs in readers.items():
                if (k2, j2) not in placed or (k2, j2) == (k, i): continue
                for i2 in rs:
                    if (k2, i2) not in placed:
                        forced.setdefault(w + 1, []).append((k2, i2))
                        for r in uses(ops[k2][i2]):          # reserve what the forced read needs
                            for x, e in need_for(ops[k2], i2, r).items():
                                if owner[x] is None: owner[x] = k2; end[k2][x] = e
        if o.port in ("rd", "wr"): port_free = w + 4
        if o.port == "rd": preads[k].append(w)
        if o.port == "rb": preads[k].pop(0)
        if o.cat == 3: id_val = o.ptr
        pos[k] += 1
        for r in owner:
            if owner[r] == k and pos[k] > end[k].get(r, -1): owner[r] = None

    keyf = (lambda k: -(len(ops[k]) - pos[k])) if prio is None else (lambda k: prio(k, pos[k], len(ops[k])))
    w = 0; empty_run = 0
    while not all(done(k) for k in names):
        word = []
        for (k, i) in forced.pop(w, []):
            if pos[k] > i: continue
            if pos[k] != i or not op_ok(k, ops[k][i], word, w): raise Stuck("forced read %s:%d at %d" % (k, i, w))
            word.append((k, ops[k][i])); commit(k, ops[k][i], w)
        for k in names:                              # an srbd whose window closes (a newer read is out) goes first
            if not done(k) and ops[k][pos[k]].port == "rb" and len(preads[k]) > 1:
                o = ops[k][pos[k]]
                if op_ok(k, o, word, w): word.append((k, o)); commit(k, o, w)
                elif w >= preads[k][1] + 3: raise Stuck("srbd window missed %s at %d" % (k, w))
        progress = True
        while progress and not (word and word[0][1].cat in (3, 12)):
            progress = False
            urgent = [k for k in names if not done(k) and preads[k]]
            for k in urgent + sorted([k for k in names if not done(k) and k not in urgent], key=keyf):
                if done(k): continue
                o = ops[k][pos[k]]
                if op_ok(k, o, word, w):
                    word.append((k, o)); commit(k, o, w); progress = True
                    if o.cat in (3, 12): break
        if any(x.d and x.d[0] == "id" and x.d[1] for _, x in word): id_val = nextcell.get(id_val)
        words.append(word)
        empty_run = 0 if word else empty_run + 1
        if empty_run > max_empty or w > 400:
            ex = Stuck("stuck at %d: %s owners %s id %s preads %s" % (w, {k: (pos[k], ops[k][pos[k]]) for k in names if not done(k)},
                                                                 owner, id_val, {k: v for k, v in preads.items() if v}))
            ex.words = words; raise ex
        w += 1
    return words


def best_schedule(T, trials=600, seed=1, nv=NV):
    """Greedy list scheduling under many deterministic random thread priorities; keep the shortest."""
    rnd = random.Random(seed)
    blocks = [[n + ".r" for n in SBLK(nv)]]
    best = None
    for trial in range(trials + 1):
        if trial == 0: pr = None
        else:
            wts = {k: rnd.random() for k in T}
            mode = trial % 3
            if mode == 0: pr = lambda k, p, n, wts=wts: -(n - p) * wts[k]
            elif mode == 1: pr = lambda k, p, n, wts=wts: wts[k]
            else: pr = lambda k, p, n, wts=wts: -(n - p) + 10 * wts[k]
        try: W = schedule(T, pr, blocks=blocks)
        except (Stuck, KeyError, IndexError): continue
        if best is None or len(W) < len(best): best = W
    assert best is not None, "no schedule found"
    return best


# ---------------- link ----------------
def link(words, nv=NV):
    """c*+ stream slots get cmem addresses in program order (ca = 0 at every sample start); direct names after.
    dmem: the SVx.r block (walked by the id pointer; SVx.w = SVx.r - 1, written after the walk) first, then the rest,
    then state pairs (write at m, read next sample at m + 1). Returns lines, cadr, cslot (addr -> base name), dadr."""
    cslot, cadr, dadr = {}, {}, {}
    n = 0
    for word in words:
        spec = next((o.c for k, o in word if o.c), None)
        if spec and spec[0] == "str": cslot[n] = spec[1]; n += 1
    for word in words:
        for k, o in word:
            if o.c and o.c[0] == "dir" and o.c[1] not in cadr: cadr[o.c[1]] = n; n += 1
    blk = SBLK(nv)
    base = 1
    for i, nm in enumerate(blk): dadr[nm + ".r"] = base + i; dadr[nm + ".w"] = base + i - 1
    dn = base + len(blk)
    states = set()
    def dname(spec): return spec[1] if spec[0] == "dir" else spec[2]
    for word in words:
        for k, o in word:
            nms = ([dname(o.d)] if o.d else []) + ([o.ptr] if o.cat == 3 else [])
            for nm in nms:
                if nm in dadr: continue
                if nm.endswith(".r") or nm.endswith(".w"): states.add(nm[:-2])
                else: dadr[nm] = dn; dn += 1
    for sname in sorted(states):
        dadr[sname + ".w"] = dn; dadr[sname + ".r"] = dn + 1; dn += 2
    assert n <= 256 and dn <= 256, (n, dn)
    lines, idp = [], None
    for word in words:
        parts = []
        inc = False
        for k, o in word:
            t = o.txt
            if o.cat == 3:
                t = t.replace("%i", "%02x" % dadr[o.ptr]); idp = dadr[o.ptr]
            if o.c: t = t.replace("%c", "c(%02x)" % cadr[o.c[1]] if o.c[0] == "dir" else "c*+")
            if o.d:
                if o.d[0] == "dir": t = t.replace("%d", "d(%02x)" % dadr[o.d[1]])
                else:
                    assert idp == dadr[o.d[2]], "id pointer at %s, %s wanted %s" % (idp, o.txt, o.d[2])
                    t = t.replace("%d", "d*+" if o.d[1] else "d*"); inc = inc or o.d[1]
            parts.append((1 if o.cat in (1, 12) else o.cat, t))
        if inc: idp += 1
        c3 = [t for c, t in parts if c == 3]
        if c3: lines.append(c3[0]); continue
        c1 = [t for c, t in parts if c == 1]; c2 = [t for c, t in parts if c == 2]
        lines.append(" ; ".join(c1 + c2) if (c1 or c2) else "nop")
    lines.append("idle")
    assert len(lines) <= 256, "program too long: %d words" % len(lines)
    last_port = max(i for i, word in enumerate(words) if any(o.port in ("rd", "wr") for k, o in word))
    assert last_port <= len(lines) - 6, "delay-RAM access too close to the end (%d of %d)" % (last_port, len(lines))
    return lines, cadr, cslot, dadr


def q31(x): return max(-0x80000000, min(0x7FFFFFFF, int(round(x * 2147483648.0)))) & 0xFFFFFFFF


def init_values(nv=NV, platform="mp5"):
    v = dict(ONE16=0x10000, OINC=1 << 26, HALF=0x40000000, SIGN=0x80000000, K15=0x8000, K30=1 << 30, M16=0xFFFF,
             ZERO=0, AMASK=0xFFFFFFFF, OPH=0, PWT=0,
             RG0=0, RG2=0, RGS=q31(0.5), RGW=0, RGP=0, MREC=0x80000000)
    for vv in range(nv):
        v.update({"DL%d" % vv: 0x10000, "PH%d" % vv: 0, "XM%d" % vv: XM_SYNTH, "KD%d" % vv: q31(0.999),
                  "KT%d" % vv: 0, "LVL%d" % vv: 0, "A0%d" % vv: 0, "A1%d" % vv: 0})
    th0, th1 = (0.5, 1.0) if platform == "mp5" else (0.32, 0.0)
    for L in "LR":
        v.update({"VIN" + L: q31(1.0), "VXF" + L: q31(0.5), "FM1" + L: 0x80000000, "FMQ" + L: q31(-1.0),
                  "FMF" + L: q31(-0.5), "FF1" + L: q31(0.5), "FF2" + L: q31(0.5),
                  "TH0" + L: q31(th0), "TH1" + L: q31(th1)})
    return v


def cmem_init(cadr, cslot, nv=NV, platform="mp5"):
    v = init_values(nv, platform)
    out = [0] * 256
    for n, a in cadr.items(): out[a] = v[n] & 0xFFFFFFFF
    for a, n in cslot.items(): out[a] = v[n] & 0xFFFFFFFF
    return out


class Image:
    def __init__(s, nv=NV, platform="mp5", trials=600):
        T = threads(nv)
        s.words = best_schedule(T, trials, nv=nv)
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


_cache = {}
def build(nv=NV, platform="mp5"):
    if (nv, platform) not in _cache: _cache[(nv, platform)] = Image(nv, platform)
    return _cache[(nv, platform)]


# ---------------- the H8 side: generated assembler (src/keys.asm includes it through tools/rosetta_build.py) ----------------
HOST_LISTS = lambda nv: {
    "DL": ["DL%d" % v for v in range(nv)], "PH": ["PH%d" % v for v in range(nv)], "KD": ["KD%d" % v for v in range(nv)],
    "KT": ["KT%d" % v for v in range(nv)], "XM": ["XM%d" % v for v in range(nv)],
    "FF": ["FF1L", "FF2L", "FF1R", "FF2R"], "FMF": ["FMFL", "FMFR"], "FMQ": ["FMQL", "FMQR"],
    "VIN": ["VINL", "VINR"], "VXF": ["VXFL", "VXFR"], "TH0": ["TH0L", "TH0R"], "TH1": ["TH1L", "TH1R"],
    "RGIN": ["RG0", "RG2"], "RGWAVE": ["RGS", "RGW", "RGP"]}
HOST_ONE = ("MREC", "AMASK", "PWT")


def gen_asm(im, platform="mp5"):
    p, c = image_bytes(im.prog, im.cmem)
    out = ["; generated by tools/keysdsp.py: DDS-DSP SAMPLER program image, coefficient addresses, value tables",
           "        .equ KEYS_PROG_LEN, %d" % len(p), "        .equ KEYS_CMEM_LEN, %d" % len(c),
           "        .equ KNV, %d" % im.nv]
    def one(name):
        a = im.addrs(name); assert len(a) == 1, (name, a); return a[0]
    for name in HOST_ONE: out.append("        .equ KA_%s, %d" % (name, one(name)))
    for k, names in HOST_LISTS(im.nv).items():
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
    print("program: %d words (incl. idle), %d cmem words, %d dmem words" %
          (len(im.lines), len(im.cadr) + len(im.cslot), max(im.dadr.values()) + 1))
    t = tms57sim.TMS()
    t.load(ST0, ST1, im.prog, im.cmem)
    sg = lambda v: v - (1 << 24) if v & 0x800000 else v
    out = []
    def run(n, si=(0, 0, 0, 0)):
        for _ in range(n): out.append(t.sample(si))
    def upd(name, val):
        for a in im.addrs(name): t.host_update(a, val); run(2)
    def env(v, level, k=0.01):                      # what the H8 sends: KD = 1 - K, KT = K * T
        upd("KD%d" % v, q31(1 - k)); upd("KT%d" % v, q31(k * level))
    def freq(y):
        z = [i for i in range(1, len(y)) if y[i - 1] < 0 <= y[i]]
        return (len(z) - 1) / ((z[-1] - z[0]) / FS) if len(z) > 2 else 0
    def rms(y): return math.sqrt(sum(v * v for v in y) / len(y)) / 0x800000
    def tail(n, ch=0): return [sg(o[ch]) for o in out[-n:]]
    def tone(y, fr): return 2 * math.hypot(sum(v * math.cos(2 * math.pi * fr * i / FS) for i, v in enumerate(y)),
                                           sum(v * math.sin(2 * math.pi * fr * i / FS) for i, v in enumerate(y))) / len(y) / 0x800000
    def filt(fc, q=1.0):
        f = 2 * math.sin(math.pi * fc / FS)
        for L in "LR": upd("FF1" + L, q31(f)); upd("FF2" + L, q31(f)); upd("FMF" + L, q31(-f)); upd("FMQ" + L, q31(-q))
    ok = True
    def check(name, cond, info):
        nonlocal ok
        print("%-32s %s  %s" % (name, "OK " if cond else "BAD", info)); ok = ok and cond
    root = FS / 64
    filt(7000)
    for L in "LR": upd("VXF" + L, q31(1.0))
    run(3000)                                       # SYNTH sine: 64 samples are enough
    check("silent at load", rms(tail(1000)) < 1e-4, "rms %.5f" % rms(tail(1000)))
    upd("PH0", 0); env(0, 1.0); run(6000)
    f1, r1 = freq(tail(4000)), rms(tail(4000))
    check("synth sine at the root", abs(f1 - root) < 2, "%.1f Hz (expect %.1f), rms %.3f" % (f1, root, r1))
    l, r = tail(4000, 0), tail(4000, 1)
    check("SPREAD 0: L = R", max(abs(a - b) for a, b in zip(l, r)) < 64, "max diff %d" % max(abs(a - b) for a, b in zip(l, r)))
    y = tail(4096)
    h = [tone(y, root * k) for k in (1, 2, 3)]
    check("it is a sine (parabola)", h[1] < 0.02 * h[0] and h[2] < 0.06 * h[0], "h1 %.4f h2 %.4f h3 %.4f" % tuple(h))
    upd("DL0", note_delta(12)); run(6000)
    check("+12 semitones", abs(freq(tail(4000)) - 2 * root) < 3, "%.1f Hz" % freq(tail(4000)))
    upd("DL0", note_delta(-12)); run(8000)
    check("-12 semitones", abs(freq(tail(6000)) - root / 2) < 2, "%.1f Hz" % freq(tail(6000)))
    upd("DL0", note_delta(7)); run(6000)
    check("+7 semitones", abs(freq(tail(4000)) - root * 2 ** (7 / 12)) < 3, "%.1f Hz" % freq(tail(4000)))
    env(0, 0.0, 0.0005); run(30000)
    check("release to silence", rms(tail(2000)) < 1e-3, "rms %.5f" % rms(tail(2000)))
    # 8 voices: an 8-note chord, every pitch present, L / R groups, then crossfeed
    chord = [0, 4, 7, 12, 16, 19, 24, 28]
    for L in "LR": upd("VXF" + L, 0); upd("VIN" + L, q31(0.5))
    for v, s_ in enumerate(chord):
        upd("DL%d" % v, note_delta(s_ - 12)); upd("PH%d" % v, 0); env(v, 0.5)       # the H8 caps a voice at 0.5
    run(8000)
    yl, yr = tail(8192, 0), tail(8192, 1)
    al = [tone(yl, root * 2 ** ((s_ - 12) / 12)) for s_ in chord]
    ar = [tone(yr, root * 2 ** ((s_ - 12) / 12)) for s_ in chord]
    check("8-voice chord, even voices L", all(al[v] > 0.02 for v in range(0, 8, 2)) and all(al[v] < 0.005 for v in range(1, 8, 2)),
          " ".join("%.3f" % a for a in al))
    check("8-voice chord, odd voices R", all(ar[v] > 0.02 for v in range(1, 8, 2)) and all(ar[v] < 0.005 for v in range(0, 8, 2)),
          " ".join("%.3f" % a for a in ar))
    for L in "LR": upd("VXF" + L, q31(0.5))
    run(10000)
    yl = tail(8192, 0); al = [tone(yl, root * 2 ** ((s_ - 12) / 12)) for s_ in chord]
    check("SPREAD 0: all 8 in L, even", all(a > 0.02 for a in al) and max(al) < 1.3 * min(al), " ".join("%.3f" % a for a in al))
    for v in range(8): env(v, 0.0, 0.002)
    run(20000)
    # saw wave, filter, pulse width
    for L in "LR": upd("VIN" + L, q31(1.0)); upd("VXF" + L, q31(1.0))
    upd("RGS", 0); upd("RGW", q31(0.5)); upd("DL0", note_delta(0)); upd("PH0", 0); env(0, 1.0)
    run(4000)
    r4 = rms(tail(3000)); y = tail(4096)
    h = [tone(y, root * k) for k in (1, 2, 3)]
    check("saw wave", abs(freq(tail(3000)) - root) < 2 and 0.4 < h[1] / h[0] < 0.6, "rms %.3f, h2/h1 %.2f h3/h1 %.2f" % (r4, h[1] / h[0], h[2] / h[0]))
    filt(300)
    run(5000)
    r5 = rms(tail(3000))
    check("low-pass 300 Hz on the saw", r5 < r4 * 0.6, "rms %.3f (was %.3f)" % (r5, r4))
    filt(7000)
    upd("RGW", 0); upd("RGP", q31(0.5)); run(3000)
    y = tail(4096); h = [tone(y, root * k) for k in (1, 2, 3)]
    check("pulse 50%: odd harmonics", h[1] < 0.08 * h[0] and h[2] > 0.2 * h[0], "h2/h1 %.3f h3/h1 %.3f" % (h[1] / h[0], h[2] / h[0]))
    upd("PWT", q31(0.25)); run(3000)
    y = tail(4096); h = [tone(y, root * k) for k in (1, 2, 3)]
    check("pulse width 25%: even harmonics", h[1] > 0.3 * h[0], "h2/h1 %.3f" % (h[1] / h[0]))
    upd("PWT", 0)
    env(0, 0.0, 0.002); run(20000)
    # SAMPLE: record a 220 Hz input on si1_l, then play it back an octave up
    upd("RGP", 0); upd("RG2", q31(0.5)); upd("AMASK", 0); upd("AMASK", 0xFFFFFFFF)
    for n in range(66000):
        x = int(0x400000 * math.sin(2 * math.pi * 220 * n / FS)) & 0xFFFFFF
        out.append(t.sample((0, 0, x, 0)))
    upd("RG2", 0); upd("MREC", 0)
    for v in range(8): upd("XM%d" % v, 0xFFFFFFFF)
    for L in "LR": upd("TH1" + L, 0); upd("TH0" + L, 0)
    upd("DL0", note_delta(12)); upd("PH0", 0); env(0, 1.0); run(8000)
    check("SAMPLE: recorded 220 Hz, +12", abs(freq(tail(6000)) - 440) < 3, "%.1f Hz, rms %.3f" % (freq(tail(6000)), rms(tail(6000))))
    run(70000)
    check("SAMPLE stays (no overwrite)", abs(freq(tail(6000)) - 440) < 3 and rms(tail(6000)) > 0.02, "%.1f Hz, rms %.3f" % (freq(tail(6000)), rms(tail(6000))))
    # THRU / so1
    env(0, 0.0, 0.002); run(20000)
    for L in "LR": upd("TH1" + L, q31(1.0)); upd("TH0" + L, q31(0.5))
    for n in range(3000):
        x = int(0x200000 * math.sin(2 * math.pi * 220 * n / FS)) & 0xFFFFFF
        out.append(t.sample((0, 0, x, x)))
    r8 = rms(tail(2000))
    check("thru si1 -> so0", abs(r8 - 0x200000 / math.sqrt(2) / 0x800000) < 0.01, "rms %.3f" % r8)
    check("so1 = si1 copy", tail(1, 2)[0] == sg(x), "so1_l %d si1_l %d" % (tail(1, 2)[0], sg(x)))
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
    if "--count" in sys.argv:
        for nv in (6, 7, 8):
            T = threads(nv)
            n1 = sum(1 for k in T for o in T[k][1] if o.cat in (1, 3, 12)); n2 = sum(1 for k in T for o in T[k][1] if o.cat == 2)
            W = best_schedule(T, 200)
            print("NV=%d: cat1+3+fused %d, cat2 %d -> %d words (+ idle)" % (nv, n1, n2, len(W)))
    if "--list" in sys.argv:
        im = build()
        for i, l in enumerate(im.lines): print("%3d  %06X  %s" % (i, im.prog[i], l))
    if "--test" in sys.argv:
        w = sys.argv[sys.argv.index("--wav") + 1] if "--wav" in sys.argv else None
        selftest(w)
