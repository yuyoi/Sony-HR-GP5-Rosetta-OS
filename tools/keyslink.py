"""MIDI link to the DDS-DSP SAMPLER (Rosetta 0.4.3+, HR-MP5 build): open / leave the page, set its settings, upload and
load a TMS57002 program + coefficient image, poke single coefficients, go back to the ROM image.

Messages (any MIDI channel):
  CC 119          >= 64 open the DDS-DSP SAMPLER page, < 64 leave it (from any ROSETTA page)
  CC 20 .. 38     the page's settings in order (SETTINGS below), value clamped to the setting's range
  notes, CC 64, CC 120 / 123   play the voices (all 16 channels)
  SysEx F0 7D 52 cmd .. F7  (7D = non-commercial, 52 = 'R'):
    10 oh ol n..  image bytes as nibble pairs (high first) at offset oh * 128 + ol of the H8's buffer
    11            load the buffer into the TMS57002 (ST0, ST1, 256 words, then 256 coefficients)
    12 ah al n x8 coefficient ah * 128 + al = 32-bit value (nibbles, high first)
    13            back to the ROM image (and its settings)
    14 n v        setting n = v
Live use needs Python with mido (+ sounddevice for the audio helpers): py -3.10 tools/keyslink.py open | close |
set NAME V | note N [VEL] [SECS] | rom | poke ADDR HEX
"""
import os, sys, time

SETTINGS = ["PLAY", "NOTE", "CHORD", "MODE", "SINE", "SAW", "PULSE", "WIDTH", "SOURCE", "ATTACK", "DECAY", "SUSTAIN",
            "RELEASE", "CUTOFF", "RESONANCE", "SPREAD", "VOLUME", "THRU", "ROOT"]
CC_PAGE, CC_FIRST = 119, 20
HDR = [0xF0, 0x7D, 0x52]
CHUNK = 48                                   # image bytes per SysEx message (96 nibbles)


def nib(bs):
    out = []
    for b in bs: out += [(b >> 4) & 15, b & 15]
    return out


def image(prog, cmem, st0=0x010820, st1=0x000020):
    """PLOAD stream (ST0, ST1, 256 x 3 bytes) + CLOAD stream (256 x 4 bytes), the H8 buffer layout."""
    p = bytearray(st0.to_bytes(3, "big") + st1.to_bytes(3, "big"))
    for w in list(prog) + [0] * (256 - len(prog)): p += (w & 0xFFFFFF).to_bytes(3, "big")
    for v in list(cmem) + [0] * (256 - len(cmem)): p += (v & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(p)


def cc_page(on): return [0xB0, CC_PAGE, 127 if on else 0]
def cc_set(name, v): return [0xB0, CC_FIRST + SETTINGS.index(name), max(0, min(127, int(v)))]
def sx_load(): return HDR + [0x11, 0xF7]
def sx_rom(): return HDR + [0x13, 0xF7]
def sx_set(name, v): return HDR + [0x14, SETTINGS.index(name), max(0, min(127, int(v))), 0xF7]
def sx_coef(addr, value): return HDR + [0x12, (addr >> 7) & 1, addr & 0x7F] + nib((value & 0xFFFFFFFF).to_bytes(4, "big")) + [0xF7]


def sx_upload(prog, cmem, st0=0x010820, st1=0x000020):
    img = image(prog, cmem, st0, st1)
    return [HDR + [0x10, off >> 7, off & 0x7F] + nib(img[off:off + CHUNK]) + [0xF7] for off in range(0, len(img), CHUNK)]


# ---------------- live (mido) ----------------
class Link:
    def __init__(s, port=None):
        import mido
        s.mido = mido
        name = port or next((n for n in mido.get_output_names() if "Focusrite" in n), None)
        assert name, "no MIDI out (want a Focusrite port): %s" % mido.get_output_names()
        s.out = mido.open_output(name)

    def raw(s, bs, wait=0.0):
        if bs[0] == 0xF0: s.out.send(s.mido.Message("sysex", data=bs[1:-1]))
        else: s.out.send(s.mido.Message.from_bytes(bs))
        time.sleep(wait + len(bs) * 0.00032)           # 31250 baud

    def page(s, on): s.raw(cc_page(on), 1.5)           # the page load masks MIDI for a moment
    def set(s, name, v): s.raw(cc_set(name, v), 0.05)
    def note(s, n, vel=100, ch=0): s.out.send(s.mido.Message("note_on", note=n, velocity=vel, channel=ch))
    def off(s, n, ch=0): s.out.send(s.mido.Message("note_off", note=n, velocity=0, channel=ch))
    def coef(s, addr, value): s.raw(sx_coef(addr, value), 0.03)
    def rom(s): s.raw(sx_rom(), 1.5)

    def upload(s, prog, cmem, st0=0x010820, st1=0x000020, load=True):
        for m in sx_upload(prog, cmem, st0, st1): s.raw(m, 0.02)
        if load: s.raw(sx_load(), 1.5)


if __name__ == "__main__":
    a = sys.argv[1:]
    L = Link()
    if a[0] == "open": L.page(True)
    elif a[0] == "close": L.page(False)
    elif a[0] == "set": L.set(a[1].upper(), int(a[2]))
    elif a[0] == "rom": L.rom()
    elif a[0] == "poke": L.coef(int(a[1], 0), int(a[2], 16))
    elif a[0] == "note":
        n = int(a[1]); vel = int(a[2]) if len(a) > 2 else 100; secs = float(a[3]) if len(a) > 3 else 1.0
        L.note(n, vel); time.sleep(secs); L.off(n)
    print("sent", " ".join(a))
