# Build Rosetta OS from a stock Sony dump:
#   python -I tools/rosetta_build.py            -> HR-GP5 (Ver1.03E dump)  build/hrgp5_rosetta_os.bin (+ x4, listings)
#   python -I tools/rosetta_build.py mp5        -> HR-MP5 (Ver1.11 dump)   build/hrmp5_rosetta_os.bin
# Steps: strip factory presets (programs 0-99 -> one INIT program), TEST mode off, ROSETTA code into the freed
# page-0 space, SYSTEM:Select B = ROSET, Hex girl boot, then boot it in the emulator and screenshot.
# Model-specific addresses: PLATFORMS below + src/platform_<model>.asm.
import sys, os, hashlib, struct
sys.path.insert(0, os.path.dirname(__file__))
from h85asm import Asm
import anim_build, gp5emu

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VERSION = "0.4.0"
PRESET_LEN = 0xB0

# Everything model-specific. GP5 = HR-GP5 Ver1.03E (EPROM GP5-E1), MP5 = HR-MP5 Ver1.11 (dump rom1_11j).
PLATFORMS = {
    "gp5": dict(
        rom_in=anim_build.ROM_IN, md5=anim_build.STOCK_MD5, presets=0xB318, init_from=8,   # "Transparnt" -> INIT
        free_end=0xF7D8, rosetta_base=0xB3D0, src="platform_gp5.asm", out="hrgp5_rosetta_os.bin",
        prog_patches=[(0x1890, "a5 ac 0c b3 18 24", "5c b3 18 00 00 00"),           # prog_addr:    MOV.W #INIT,R4
                      (0x18CB, "a5 a8 0c b3 18 20", "58 b3 18 00 00 00")],          # prog_addr_dp: MOV.W #INIT,R0
        cwrite_stub="18 56 b3 11 19",                                               # JSR @0x56B3 ; PRTS
        select=(0x4840, "49 d0", 0x4842, "48 87", "48 29"), names=(0x4801, 0x4807),
        edtab=0x8044, edit_end=None,
        blocks=[(0x3C, 0x00), (0x44, 0x03), (0x4C, 0x0A), (0x5C, 0x0D), (0x78, 0x23), (0x94, 0x2B)],
        base_text="SONY 1.03E",
        play_entry=0x705F,
        anim=dict(hook=0x2558, hook_orig=bytes.fromhex("18 10 21 04 04 8d"), player=0x1F5A0, limit=0x1FFFF)),
    "mp5": dict(
        rom_in=os.path.join(ROOT, "dumps", "mp5_rom1_11j", "rom1_11j.bin"), md5="002beb9c2786500aa11818c98af6026b",
        presets=0xB7D6, init_from=0,                                                 # "Plate Hall" -> INIT
        free_end=0xFD00, rosetta_base=0xB890, src="platform_mp5.asm", out="hrmp5_rosetta_os.bin",
        prog_patches=[(0x18AE, "a5 ac 0c b7 d6 24", "5c b7 d6 00 00 00"),
                      (0x18E9, "a5 a8 0c b7 d6 20", "58 b7 d6 00 00 00")],
        cwrite_stub="18 47 55 11 19",                                               # JSR @0x4755 ; PRTS
        select=(0x485E, "49 ee", 0x4860, "48 a5", "48 47"), names=(0x481F, 0x4825),
        edtab=0x860E, edit_end=0x35,                                                 # id 0x35 = LEVEL
        blocks=[(0x38, 0x00), (0x4A, 0x02), (0x74, 0x1F), (0x86, 0x21)],             # EQ1, EFCT1, EQ2, EFCT2
        base_text="SONY MP5 1.11",
        play_entry=0x7069,
        anim=dict(hook=0x2576, hook_orig=bytes.fromhex("18 10 37 04 04 8d"), player=None, limit=0xFD00),
        keys=dict(stubs=[(0x1FFB8, "15 fd 10 06 04 18 47 5a 11 19"),                # KST_CW: FD10 = 4, JSR @0x475A, PRTS
                         (0x1FFC2, "15 fd 10 06 04 18 46 90 11 19"),                # KST_PL: program load (PLOAD stays on)
                         (0x1FFCC, "15 fd 10 06 04 18 46 7d 18 46 d1 11 19")],      # KST_CL: + coefficients, release
                  gates=[(0x14755, "gate_cw"), (0x146DC, "gate_cw2"), (0x1468B, "gate_pl"), (0x14678, "gate_cl"),
                         (0x13E43, "gate_mr")])),
}
KEYS_ITEM = """        .dw     l_keys
        .db     K_SUB, 0
        .dw     pg_keys, 0
"""


def root_key(k, model):
    """root-menu row keys: the MP5 root has DDS-DSP SAMPLER as row A, the rest one row down"""
    if model in PLATFORMS and "keys" in PLATFORMS[model] and k in "ABCDE": return chr(ord(k) + 1)
    return k


def patch(rom, addr, old, new):
    old = bytes.fromhex(old) if isinstance(old, str) else old
    new = bytes.fromhex(new) if isinstance(new, str) else new
    assert len(old) == len(new), hex(addr)
    assert rom[addr:addr + len(old)] == old, "patch %05X: expected %s, found %s" % (addr, old.hex(" "), rom[addr:addr + len(old)].hex(" "))
    rom[addr:addr + len(new)] = new


def big_width(rom, text):
    w = 0
    for ch in text.encode():
        p = 0x10000 | ((rom[0x112F4 + (ch & 0x7F) * 2] << 8) | rom[0x112F5 + (ch & 0x7F) * 2])
        w += rom[p]
    return w


def edit_table_len(rom, edtab):
    n = 0
    while True:
        e = edtab + n * 8
        p = (rom[e] << 8) | rom[e + 1]
        name = rom[e + 2:e + 8]
        if not (0x7000 <= p < 0xB000 and name[-1] == 0 and all(32 <= c < 127 for c in name[:name.index(0)])): return n
        n += 1


def gen_tables(rom, pf):
    """LFO rate table (0.05-15 Hz at a 100 Hz update), sine table, block table (offset, id base, type count)"""
    import math
    n = pf["edit_end"] if pf["edit_end"] is not None else edit_table_len(rom, pf["edtab"])
    bases = [b for o, b in pf["blocks"]] + [n]
    rates = [round(0.05 * (15 / 0.05) ** (r / 99) * 65536 / 100) for r in range(100)]
    sine = [round(127 * math.sin(2 * math.pi * i / 256)) & 0xFF for i in range(256)]
    out = ["        .align 2", "rate_tab:"]
    for i in range(0, 100, 10): out.append("        .dw     " + ", ".join(str(v) for v in rates[i:i + 10]))
    out.append("sine_tab:")
    for i in range(0, 256, 16): out.append("        .db     " + ", ".join(str(v) for v in sine[i:i + 16]))
    thr = [max(1, round(255 * 10 ** (-(24 - i) * 2 / 20))) for i in range(1, 25)]   # ADC p-p thresholds, 2 dB apart
    out.append("thr_tab:")
    out.append("        .db     " + ", ".join(str(v) for v in thr))
    out.append("        .align 2")
    out.append("blk_tab:")
    for k, (o, b) in enumerate(pf["blocks"]): out.append("        .db     %d, %d, %d, 0" % (o, b, bases[k + 1] - b))
    return "\n".join(out) + "\n"


def build(out, model="gp5"):
    pf = PLATFORMS[model]
    rom = bytearray(open(pf["rom_in"], "rb").read())
    assert hashlib.md5(rom).hexdigest() == pf["md5"], "needs the stock %s dump" % model
    presets = pf["presets"]
    free0, free_end = presets + PRESET_LEN, pf["free_end"]

    # 1. presets -> one INIT program; programs 0-99 all point at it
    rec = bytearray(rom[presets + pf["init_from"] * PRESET_LEN:][:PRESET_LEN])
    rec[9:27] = b"INIT".ljust(18)
    rom[presets:presets + PRESET_LEN] = rec
    rom[free0:free_end] = b"\xff" * (free_end - free0)
    for addr, old, new in pf["prog_patches"]: patch(rom, addr, old, new)

    # 2. TEST mode (A + SYSTEM at power on) off: skip the second key-combo check
    patch(rom, 0x0256, "54 01 18 12", "13 00 05 e4")                 # PJMP @main_init

    # 3. ROSETTA code in the freed page-0 space
    src = open(os.path.join(ROOT, "src", pf["src"]), encoding="utf-8").read()
    ros = open(os.path.join(ROOT, "src", "rosetta.asm"), encoding="utf-8").read()
    ros = ros.replace('v_base:   .asciz "SONY 1.03E"', 'v_base:   .asciz "%s"' % pf["base_text"])
    has_keys = "keys" in pf
    ros = ros.replace(";@KEYS_ITEM\n", KEYS_ITEM if has_keys else "")
    src += "\n        .equ ROOT_N, %d\n" % (6 if has_keys else 5)
    src += "\n" + ros
    src += "\n" + open(os.path.join(ROOT, "src", "lfo.asm"), encoding="utf-8").read()
    src += "\n" + open(os.path.join(ROOT, "src", "inputtest.asm"), encoding="utf-8").read()
    if has_keys:                                                     # DDS-DSP SAMPLER (src/keys.asm + tools/keysdsp.py)
        import keysdsp
        src += "\n" + open(os.path.join(ROOT, "src", "keys.asm"), encoding="utf-8").read()
        src += "\n" + keysdsp.gen_asm(keysdsp.Image(platform=model), model)
    else:
        src += "\nkeys_apply:\nkeys_tick:\n        RTS\n"
    src += "\n" + gen_tables(rom, pf)
    big_col = (38 - big_width(rom, "ROSETTA")) // 2 if model == "gp5" else 0
    a = Asm({"ROSETTA_BASE": pf["rosetta_base"], "BIG_COL": big_col})
    code = a.assemble(src)
    lo, hi = min(code), max(code)
    assert free0 <= lo and hi < free_end, "rosetta code outside the free area"
    for k, v in code.items():
        assert rom[k] == 0xFF; rom[k] = v
    bad = a.check(bytes(rom), 0)
    assert not bad, bad
    sy = a.syms

    # 3a. page-1 stub so page-0 Rosetta can PJSR Sony's live-coefficient routine (it ends in RTS, not PRTS)
    patch(rom, sy["CWRITE_P1"], "ff ff ff ff ff", pf["cwrite_stub"])

    # 3a1. DDS-DSP SAMPLER: page-1 stubs past the gates, Sony's TMS57002 routine entries -> gate_* (PJMP, page 0)
    if has_keys:
        for addr, code in pf["keys"]["stubs"]:
            n = len(bytes.fromhex(code))
            patch(rom, addr, b"\xff" * n, code)
        for addr, lab in pf["keys"]["gates"]:
            patch(rom, addr, "15 fd 10 06 04", bytes([0x13, 0x00, sy[lab] >> 8, sy[lab] & 0xFF, 0x00]))

    # 3a2. CPU A/D: Sony scans AN0-AN1 (battery, pedal); scan AN0-AN3 so AN2/AN3 can be the raw L/R input meters
    patch(rom, 0x0208, "15 ff e8 06 31", "15 ff e8 06 33")         # MOV.B #0x33,@ADCSR

    # 3b. 5 ms system tick (vector 0xC0) -> lfo_isr, which then jumps on to Sony's handler
    patch(rom, 0x00C0, "00 00 04 fd", bytes([0, 0, sy["lfo_isr"] >> 8, sy["lfo_isr"] & 0xFF]))

    # 4. SYSTEM:Select: B (CLOCK) -> ROSET, C (MEMRY) -> nothing yet
    sb, sb_old, sc, sc_old, stay = pf["select"]
    patch(rom, sb, sb_old, bytes([sy["ros_entry"] >> 8, sy["ros_entry"] & 0xFF]))
    patch(rom, sc, sc_old, stay)                                     # C -> stay on SYSTEM:Select
    patch(rom, pf["names"][0], b"CLOCK", b"ROSET")
    patch(rom, pf["names"][1], b"MEMRY", b"     ")

    # 4b. No Sony screens: Sony's PLAY loop (entered by JMP from boot and from every screen's exit) -> ROSETTA,
    #     the stock boot logo is skipped right after Hex girl and its 700-unit hold is cut to 1
    patch(rom, pf["play_entry"], "04 04 8d", bytes([0x10, sy["ros_entry"] >> 8, sy["ros_entry"] & 0xFF]))
    patch(rom, 0x0653, "58 02 bc", "58 00 01")                       # MOV.W #1,R0 (hold)

    # 5. Hex girl boot (GP5: page-1 free space; MP5: right after the Rosetta code in page 0)
    an = dict(pf["anim"])
    if an["player"] is None: an["player"] = (hi + 16) & ~15
    an["hook_tail"] = b"\x19\x00"                                   # RTS after the player: no stock logo
    px, durs = anim_build.load_frames(os.path.join(ROOT, "frames", "rosetta_boot.gif"))
    holds = [round(dd / 50.0) if dd else 1 for dd in durs]
    anim_build.build(px, holds, 0x4B, out, rom=rom, bootflag=True, **an)
    final = bytearray(open(out, "rb").read())
    with open(out[:-4] + "_rosetta.lst", "w", encoding="utf-8") as f:
        f.write(a.listing(bytes(final), 0) + "\n")
    with open(out[:-4] + "_syms.txt", "w", encoding="utf-8") as f:
        for k, v in sorted(sy.items(), key=lambda kv: kv[1]):
            f.write("%05X %s\n" % (v, k))
    used = hi - lo + 1
    print("rosetta v%s (%s): page-0 code %d B @%05X, page-0 free left %d B" % (VERSION, model, used, lo, free_end - hi - 1))
    return final, sy


def new_emu(img, model):
    e = gp5emu.Emu(img)
    if model != "gp5":
        pf = PLATFORMS[model]
        e.strict = [(pf["presets"] + PRESET_LEN, pf["free_end"])]   # Rosetta + boot player live in the freed presets
        e.font = open(PLATFORMS["gp5"]["rom_in"], "rb").read()     # stand-in for the LCD's own character ROM
    return e


def emu_tour(img, shots, model="gp5"):
    os.makedirs(shots, exist_ok=True)
    for f in os.listdir(shots):
        if f.endswith(".png"): os.remove(os.path.join(shots, f))
    e = new_emu(img, model)
    e.run(1_500_000)
    out = []
    def snap(name):
        p = os.path.join(shots, name + ".png"); e.png(p); out.append((name, e.text().splitlines()))
    steps = [("SYSTEM", None), ("B", "01_root"), ("dial+1", "02_root_tempo"), ("dial+5", "03_root_about_scroll"),
             (root_key("D", model), "04_boot_page"), ("EDIT", "05_boot_edit"), ("dial-1", "06_boot_off"), ("EXIT", "07_boot_edit_off"),
             ("EXIT", "08_back_root"), (root_key("A", model), "09_mod"), ("dial+1", None), ("EDIT", "10_mod_rate_edit"),
             ("dial+7", "11_rate_27"), ("EXIT", None), ("dial+3", "12_mod_scrolled"), ("EXIT", None),
             (root_key("E", model), "13_about"), ("EXIT", None), (root_key("C", model), "15_input"), ("E", "15b_input_source"), ("dial-1", "16_input_src_si0"),
             ("EXIT", None), ("EXIT", None), ("EXIT", "14_back_to_system")]
    for k, name in steps:
        if k.startswith("dial"): e.dial(int(k[4:]), 150000)
        else: e.press(k)
        if name: snap(name)
    ram = bytes(e.ram)
    return out, ram


def emu_boots_into_rosetta(img, model, sy):
    """power-on lands in ROSETTA (top menu), never on Sony's PLAY screen; EXIT there stays in ROSETTA"""
    e = new_emu(img, model); e.run(1_500_000)
    assert (e.ram[0x7010] << 8 | e.ram[0x7011]) == sy["pg_root"], "not in ROSETTA after boot"
    e.press("EXIT"); e.press("EXIT")
    assert (e.ram[0x7010] << 8 | e.ram[0x7011]) == sy["pg_root"], "EXIT left ROSETTA"
    return True


def emu_boot_skip(img, ram, model="gp5"):
    """second power-on with HEX GIRL = OFF in battery RAM: the animation must be skipped"""
    e = new_emu(img, model); e.ram[:] = ram
    assert ram[0x7000:0x7003] == b"RS\x00", "settings not saved: %r" % ram[0x7000:0x7003]
    e.run(400_000)
    return e.text().splitlines()[0]


def emu_input_test(img, model="gp5"):
    """INPUT page: SOURCE re-aims the DSP meter (c(05) = 0x1F / 0x27 << 24 through Sony's routine, Sony's 0x21 back on
    re-entry); ADC L / R bars follow the CPU A/D AN2 / AN3 (emulated as a swinging value / a constant)."""
    import dsplog
    e = new_emu(img, model); e.run(1_500_000)
    assert e.rom[0x020C] == 0x33, "A/D scan AN0-AN3 patch missing"
    e.dsplog = []
    for k in ("SYSTEM", "B", root_key("C", model)): e.press(k)   # ROSETTA -> INPUT
    swing = [0x40, 0xC0]                                 # AN2: p-p 0x80 -> ~19 segments; AN3 constant -> 0
    for i in range(40):
        e.adc[0xFFE4] = swing[i & 1]; e.adc[0xFFE6] = 0x80
        e.run(20_000)
    lv = (e.ram[0x7060], e.ram[0x7061])
    assert lv[0] >= 15 and lv[1] == 0, lv
    e.press("E"); e.dial(-1, 150000)                     # SOURCE -> SI0
    e.press("EXIT"); e.press("E"); e.dial(2, 150000)     # -> SI1 R
    e.press("EXIT"); e.press("EXIT"); e.press("SYSTEM"); e.press("B")   # SYSTEM:Select -> ROSET re-enters
    tms, dsp2 = dsplog.decode(e.dsplog)
    ups = [v >> 24 for a, v in tms.updates if a == 5]
    if model == "gp5":
        assert ups[0] == 0x21 and 0x1F in ups and 0x27 in ups and ups[-1] == 0x21 and \
            ups.index(0x1F) < ups.index(0x27), ["%02X" % u for u in ups]
    else:
        assert not ups, "c(05) is not known to be the meter pointer on %s: must not be written" % model
    assert not [a for a, v in tms.updates if a in (8, 9)], "c(08)/c(09) must not be touched any more"
    return lv, ups


def emu_keys_test(img, model, sy):
    """DDS-DSP SAMPLER end to end: open the page -> our program + coefficients go to the TMS57002 (Sony's writers gated),
    PLAY on -> the H8's coefficient writes, replayed into tools/tms57sim.py, play the boot sine at fs / 64; leaving the
    page -> Sony's program-change sequence reloads Sony's own TMS57002 program."""
    import dsplog, keysdsp, tms57sim, math
    im = keysdsp.Image(platform=model)
    e = new_emu(img, model); e.run(1_500_000)
    e.dsplog = []
    mark = []
    skips = {sy[l]: l for l in ("gcw1", "gcx1", "gpl1", "gcl1", "gmr1")}
    hits = {}
    def tr(em, pc):
        if pc == sy["kld_done"] and not mark: mark.append(len(em.dsplog))
        if pc in skips: hits[skips[pc]] = hits.get(skips[pc], 0) + 1
    e.trace = tr
    for k in ("SYSTEM", "B", "A"): e.press(k)                        # ROSETTA -> DDS-DSP SAMPLER
    assert (e.ram[0x7010] << 8 | e.ram[0x7011]) == sy["pg_keys"], "not on the DDS-DSP SAMPLER page"
    assert e.ram[0x70A0] == 1 and mark, "K_OWN not set / program not loaded"
    tms, _ = dsplog.decode(e.dsplog[:mark[0]])
    assert tms.st0 == keysdsp.ST0 and tms.st1 == keysdsp.ST1, (hex(tms.st0 or 0), hex(tms.st1 or 0))
    assert tms.prog[:len(im.prog)] == im.prog, "program image differs"
    assert all(tms.cmem.get(i, 0) == im.cmem[i] for i in range(256)), "coefficient image differs"
    e.press("EDIT"); e.dial(1, 150000); e.press("EXIT")              # PLAY -> ON
    for i in range(40):                                              # attack -> decay switch in keys_tick, and
        if i in (5, 15, 25):                                         # Sony parameter changes (as the LFO does): gated
            for o in (0x3A, 0x3C, 0x4C, 0x4E, 0x76, 0x8A): e.ram[0x1280 + o] ^= 0x03
        e.run(20000)
    tms2, _ = dsplog.decode(e.dsplog)
    assert tms2.loads == tms.loads, "a program load while KEYS owns the DSP"
    assert hits.get("gmr1", 0) > 0 and hits.get("gcw1", 0) + hits.get("gcx1", 0) > 0, "gates never hit: %s" % hits
    # replay into the simulator: our image, the boot sine fills the RAM, then every H8 update after the load
    after = tms2.updates[len(tms.updates):]
    import re as _re
    host = set()
    for n in set(im.cslot.values()) | set(im.cadr):
        if _re.fullmatch(r"(DL|PH|T|K|SL|AL|PL|TL|PWT|PANL|PANR)\d|FF[12][LR]|FM[FQ][LR]|WET[LR]|DRY[LR]|TH[01][LR]|G[0-3]|GS|REC|AMASK", n):
            host.update(im.addrs(n))
    stray = sorted(set(a for a, v in after) - host)
    assert not stray, "writes to non-parameter coefficients while KEYS owns the DSP (gate leak): %s" % stray
    t = tms57sim.TMS(); t.load(tms.st0, tms.st1, tms.prog, [tms.cmem.get(i, 0) for i in range(256)])
    for _ in range(66000): t.sample()
    for a, v in after:
        t.host_update(a, v); t.sample(); t.sample()
    y = []
    for _ in range(6000): y.append(t.sample()[0])
    y = [v - (1 << 24) if v & 0x800000 else v for v in y[2000:]]
    z = [i for i in range(1, len(y)) if y[i - 1] < 0 <= y[i]]
    f = (len(z) - 1) / ((z[-1] - z[0]) / 44100.0) if len(z) > 2 else 0
    rms = math.sqrt(sum(v * v for v in y) / len(y)) / 0x800000
    assert abs(f - 44100 / 64) < 2 and rms > 0.05, (f, rms)
    # leave the page: K_OWN off, Sony reloads its program
    mark = len(e.dsplog)
    e.press("EXIT")
    for _ in range(100): e.run(20000)
    assert e.ram[0x70A0] == 0, "K_OWN still set"
    tms3, _ = dsplog.decode(e.dsplog)
    assert tms3.loads > tms2.loads, "Sony did not reload its TMS57002 program"
    e0 = new_emu(img, model); e0.dsplog = []; e0.run(1_500_000)
    ref, _ = dsplog.decode(e0.dsplog)
    assert tms3.prog == ref.prog, "Sony's program after KEYS differs from the boot program"
    return dict(our_updates=len(after), gated=hits, pitch=round(f, 1), rms=round(rms, 3),
                sony_reload_words=len(tms3.prog))


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "gp5"
    out = os.path.join(ROOT, "build", PLATFORMS[model]["out"])
    img, sy = build(out, model)
    shots, ram = emu_tour(bytes(img), os.path.join(ROOT, "out", "shots" if model == "gp5" else "shots_" + model), model)
    for name, lines in shots:
        print("--", name)
    print("power-on with HEX GIRL off, top text row after 0.27 s:", repr(emu_boot_skip(bytes(img), ram, model)))
    print("boots into ROSETTA, EXIT stays:", emu_boots_into_rosetta(bytes(img), model, sy))
    lv, ups = emu_input_test(bytes(img), model)
    print("INPUT page: ADC L/R segments", lv, " c(05) sent:", " ".join("%02X" % u for u in ups))
    if "keys" in PLATFORMS[model]:
        print("DDS-DSP SAMPLER:", emu_keys_test(bytes(img), model, sy))
    print("x4 md5", hashlib.md5(open(out[:-4] + "_SST39SF040_x4.bin", "rb").read()).hexdigest())
