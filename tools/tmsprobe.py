"""TMS57002 hardware probes over the MIDI link (Rosetta 0.4.3+ on an HR-MP5 / HR-GP5 running the MP5 firmware).

Needs Python with mido + sounddevice (py -3.10 here), the unit's outputs on audio inputs and MIDI to the unit.
Each probe uploads a tiny program (tools/keyslink.py SysEx 10/11), records, and prints what the real chip did.
The findings are in docs/DSP.md ("Measured on the hardware").

  py -3.10 tools/tmsprobe.py outputs    which so* reaches which jack (tagged saws)
  py -3.10 tools/tmsprobe.py latency    srbd delay after rde: when does the new read arrive
  py -3.10 tools/tmsprobe.py spacing    minimum gap between delay-RAM accesses
  py -3.10 tools/tmsprobe.py domh       domh paired with a cat1 dmem read
Audio: ASIO (SD_ENABLE_ASIO), the first "Focusrite USB" ASIO device, channels IN_L / IN_R (0-based) = unit L / R.
"""
import os, sys, time
os.environ.setdefault("SD_ENABLE_ASIO", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import tms57sim, keyslink

ST0, ST1, FS_DSP = 0x010820, 0x000020, 48000.0
IN_L, IN_R = 2, 3                                   # Scarlett 6i6 rear line inputs 3 / 4
F1, F2 = 500.0, 1100.0


def q31(x): return max(-0x80000000, min(0x7FFFFFFF, int(round(x * 2147483648.0)))) & 0xFFFFFFFF
def db(v): return 20 * np.log10(max(v, 1e-9))


class Rec:
    def __init__(s):
        import sounddevice as sd
        s.sd = sd
        s.dev = next(i for i, d in enumerate(sd.query_devices())
                     if sd.query_hostapis()[d["hostapi"]]["name"] == "ASIO" and "Focusrite USB" in d["name"])
        s.fs = int(sd.query_devices(s.dev)["default_samplerate"])
        s.nch = sd.query_devices(s.dev)["max_input_channels"]

    def __call__(s, secs):
        x = s.sd.rec(int(secs * s.fs), samplerate=s.fs, channels=s.nch, device=s.dev, dtype="float32"); s.sd.wait()
        return x[:, IN_L].astype(float), x[:, IN_R].astype(float)

    def amp(s, y, f):
        n = len(y); t = np.arange(n) / s.fs; w = np.hanning(n)
        return 2 * abs(np.sum(y * w * np.exp(-2j * np.pi * f * t))) / np.sum(w)


def saws():                                         # d(01) = 500 Hz saw, d(02) = 1100 Hz saw
    c = [0] * 256
    c[0x11] = int(F1 / FS_DSP * 2 ** 32); c[0x13] = int(F2 / FS_DSP * 2 ** 32); c[0x30] = q31(0.25)
    return c, ["lacc c(10)", "add c(11),a", "sacc c(10)", "sacd d(01)", "lacc c(12)", "add c(13),a", "sacc c(12)", "sacd d(02)"]


def out(d, so): return ["lcak 30", "mpy d(%02x),c*" % d, "nop", "nop", "domh %s" % so]


def ram(events):
    """events: (word, op): w1 / w2 write the 500 / 1100 Hz saw at xoa 0 / 4096, r1 / r2 read xoa 16 / 4112,
    s1 / s2 = srbd of r1 / r2 into d(03) / d(06). Outputs: so0_l = r1 result, so0_r = r2 result."""
    c, L = saws()
    c[0x20] = 0; c[0x21] = 4096; c[0x22] = 16; c[0x23] = 4096 + 16
    L += ["nop", "nop"]
    slots = ["nop"] * 140
    acc = {"w1": ("20", "wre d(01),c*"), "w2": ("21", "wre d(02),c*"), "r1": ("22", "rde c*"), "r2": ("23", "rde c*")}
    for t, op in events:
        if op in acc: slots[t - 1] = "lcak " + acc[op][0]; slots[t] = acc[op][1]
        else: slots[t] = "srbd d(%02x)" % (0x03 if op == "s1" else 0x06)
    L += slots + ["nop"] * 4 + out(0x06, "so0_r") + out(0x03, "so0_l") + ["idle"]
    return tms57sim.assemble(L), c


def run(link, rec, prog, cmem):
    link.upload(prog, cmem, ST0, ST1); time.sleep(0.8)
    l, r = rec(1.0)
    return [db(rec.amp(l, F1)), db(rec.amp(l, F2)), db(rec.amp(r, F1)), db(rec.amp(r, F2))]


def main(what):
    link = keyslink.Link(); rec = Rec(); link.page(True)
    if what == "outputs":
        tags = [440.0, 610.0, 830.0, 1130.0]; so = ["so0_l", "so0_r", "so1_l", "so1_r"]
        c = [0] * 256; c[0x30] = q31(0.1); L = []
        for k in range(4):
            c[0x20 + k] = int(tags[k] / FS_DSP * 2 ** 32)
            L += ["lacc c(%02x)" % (0x10 + k), "add c(%02x),a" % (0x20 + k), "sacc c(%02x)" % (0x10 + k),
                  "sacd d(%02x)" % (0x40 + k)] + out(0x40 + k, so[k])
        link.upload(tms57sim.assemble(L + ["idle"]), c, ST0, ST1); time.sleep(0.8)
        l, r = rec(1.5)
        for k in range(4): print("%s (%4.0f Hz): left jack %6.1f dB, right jack %6.1f dB" % (so[k], tags[k], db(rec.amp(l, tags[k])), db(rec.amp(r, tags[k]))))
    elif what == "latency":
        for D in (4, 6, 8, 10, 12):
            a = run(link, rec, *ram([(2, "w1"), (30, "w2"), (60, "r1"), (60 + D, "s1"), (108, "r2"), (108 + D, "s2")]))
            print("srbd at rde + %2d: r1 %s, r2 %s" % (D, "ok" if a[0] > a[1] else "previous read", "ok" if a[3] > a[2] else "previous read"))
    elif what == "spacing":
        for g in (6, 8, 10, 12):
            a = run(link, rec, *ram([(2, "w1"), (30, "w2"), (60, "r1"), (60 + g, "r2"), (60 + g + 1, "s1"), (60 + g + 12, "s2")]))
            print("read -> read %2d words apart: second read %s" % (g, "ok" if a[3] > a[2] else "lost"))
    elif what == "domh":
        for pair in (None, "lacd d(02)", "and c(13),a"):
            c, L = saws()
            L += ["lcak 30", "mpy d(01),c*", "nop", "nop", (pair + " ; domh so0_r") if pair else "domh so0_r", "idle"]
            a = run(link, rec, tms57sim.assemble(L), c)
            print("%-12s ; domh so0_r: macc (500 Hz) %6.1f dB, the dmem cell (1100 Hz) %6.1f dB" % (pair or "(alone)", a[2], a[3]))
    link.rom()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "outputs")
