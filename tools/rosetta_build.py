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
VERSION = "0.4.5"
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
                         (0x13E43, "gate_mr")],
                  midi=(0x1F16, "15 ff dd 81", "midi_rx"))),                         # MIDI IN interrupt: MOV.B @FFDD,R1 -> JSR
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
        src += "\n" + keysdsp.gen_asm(keysdsp.build(platform=model), model)
    else:
        src += "\nkeys_apply:\nkeys_boot:\n        RTS\nkeys_tick:\n        CLR.W   R0\n        RTS\n"
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
        addr, old, lab = pf["keys"]["midi"]                                # every MIDI IN byte -> midi_rx (JSR + NOP)
        patch(rom, addr, old, bytes([0x18, sy[lab] >> 8, sy[lab] & 0xFF, 0x00]))

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
             ("EXIT", "08_back_root"), (root_key("A", model), "09_mod"), ("rock+1", None), ("EDIT", "10_mod_rate_edit"),
             ("dial+7", "11_rate_27"), ("EXIT", None), ("rock+3", "12_mod_scrolled"), ("EXIT", None),
             (root_key("E", model), "13_about"), ("EXIT", None), (root_key("C", model), "15_input"), ("E", "15b_input_source"), ("dial-1", "16_input_src_si0"),
             ("EXIT", None), ("EXIT", None), ("EXIT", "14_back_to_system")]
    for k, name in steps:
        if k.startswith("dial"): e.dial(int(k[4:]), 150000)
        elif k.startswith("rock"):
            for _ in range(abs(int(k[4:]))): e.rock(1 if int(k[4:]) > 0 else -1)
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
    im = keysdsp.build(platform=model)
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
        if _re.fullmatch(r"(DL|PH|KD|KT|XM)\d|FF[12][LR]|FM[FQ][LR]|VIN[LR]|VXF[LR]|TH[01][LR]|RG[02SWP]|MREC|AMASK|PWT", n):
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
    f_want = 44100 / 64 * keysdsp.synth_ratio(77)                    # NOTE 77 (F5) in SYNTH (sim units: fs 44.1 k)
    assert abs(f - f_want) < 2 and rms > 0.05, (f, f_want, rms)
    # CHORD -> 8 STACK while playing: all 8 voices, every pitch in the output
    n0 = len(tms2.updates)
    e.press("C"); e.dial(7, 150000); e.press("EXIT")                 # row C = CHORD, edit, last entry
    for _ in range(20): e.run(20000)
    tms4, _ = dsplog.decode(e.dsplog)
    for a, v in tms4.updates[n0:]:
        t.host_update(a, v); t.sample(); t.sample()
    for _ in range(4000): t.sample()
    y = [t.sample()[0] for _ in range(8192)]
    y = [v - (1 << 24) if v & 0x800000 else v for v in y]
    yr = [v for v in y]
    def tone(fr): return 2 * math.hypot(sum(v * math.cos(2 * math.pi * fr * i / 44100) for i, v in enumerate(y)),
                                        sum(v * math.sin(2 * math.pi * fr * i / 44100) for i, v in enumerate(y))) / len(y) / 0x800000
    stack = [0, 7, 12, 16, 19, 24, 28, 31]
    amps = [tone(44100 / 64 * keysdsp.synth_ratio(77 + s_)) for s_ in stack]
    assert all(a_ > 0.01 for a_ in amps), ["%.3f" % a_ for a_ in amps]
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
                stack8=" ".join("%.3f" % a_ for a_ in amps), sony_reload_words=len(tms3.prog))


def emu_midi_test(img, model, sy):
    """MIDI IN on the DDS-DSP SAMPLER page: notes on any channel (running status, a clock byte mid-message) take voices
    and sound at their pitch (replayed into tools/tms57sim.py); note off / velocity 0 / the sustain pedal / all notes
    off let them go; a 9th key steals the oldest voice; Sony gets no MIDI while the page is open; page closed = no
    events, Sony's MIDI as before."""
    import dsplog, tms57sim, math, keysdsp
    VK, VST, MQW, SRING = 0x7068, 0x70A4, 0x7096, 0x5A00
    e = new_emu(img, model); e.run(1_500_000)
    def send(bs, steps=250000):
        e.midi_in += bs
        e.run(len(bs) * 600 + steps)
    def ring(): return e.ram[SRING] << 8 | e.ram[SRING + 1]
    q0, r0 = e.ram[MQW], ring()
    send([0x90, 60, 100])                                            # page closed: Sony gets it, no voice later
    assert ring() != r0, "page closed: Sony ring %d -> %d" % (r0, ring())
    e.dsplog = []
    mark = []
    def tr(em, pc):
        if pc == sy["kld_done"] and not mark: mark.append(len(em.dsplog))
    e.trace = tr
    for k in ("SYSTEM", "B", "A"): e.press(k)
    assert e.ram[0x70A0] == 1 and mark, "page / program not loaded"
    e.run(300000)
    assert all(e.ram[VST + v] == 0 for v in range(8)), "a note from before the page opened plays"
    e.trace = None
    tms, _ = dsplog.decode(e.dsplog[:mark[0]])
    t = tms57sim.TMS(); t.load(tms.st0, tms.st1, tms.prog, [tms.cmem.get(i, 0) for i in range(256)])
    for _ in range(66000): t.sample()                                # the boot sine fills the wavetable
    done = [len(tms.updates)]
    def sync(n=8192):
        d, _ = dsplog.decode(e.dsplog)
        for a, v in d.updates[done[0]:]:
            t.host_update(a, v); t.sample(); t.sample()
        done[0] = len(d.updates)
        for _ in range(3000): t.sample()
        y = [t.sample()[0] for _ in range(n)]
        return [v - (1 << 24) if v & 0x800000 else v for v in y]
    def tone(y, note):
        fr = 44100 / 64 * keysdsp.synth_ratio(note)
        return 2 * math.hypot(sum(v * math.cos(2 * math.pi * fr * i / 44100) for i, v in enumerate(y)),
                              sum(v * math.sin(2 * math.pi * fr * i / 44100) for i, v in enumerate(y))) / len(y) / 0x800000
    def keys(): return {e.ram[VK + v]: e.ram[VST + v] for v in range(8) if e.ram[VK + v] != 0xFF}
    r1 = ring()
    send([0x94, 60, 100, 64, 100, 0xF8, 67, 100, 0x9F, 72, 80])     # ch 5 C4 E4 (clock mid-message) G4, ch 16 C5
    k = keys()
    assert sorted(k) == [60, 64, 67, 72] and all(s in (1, 2) for s in k.values()), k
    y = sync()
    amps = [tone(y, n) for n in (60, 64, 67, 72)]
    off = tone(y, 62)
    assert all(a > 0.01 for a in amps) and off < min(amps) / 4, (amps, off)
    send([0x84, 60, 0, 0x94, 64, 0])                                 # note off, velocity 0
    assert sorted(keys()) == [67, 72], keys()
    r1 = ring()
    send([0xB0, 64, 127, 0x80, 67, 0, 0xC0, 5])                      # pedal down: G4 held; ch 1, a program change:
    assert e.ram[VK:VK + 8].count(0xFD) == 1, list(e.ram[VK:VK + 8])
    assert ring() == r1, "Sony got MIDI while the page is open"     # Sony gets none of it
    send([0xB0, 64, 0])                                              # pedal up: G4 goes
    assert sorted(keys()) == [72], keys()
    send([0xBA, 123, 0])                                             # all notes off (ch 11)
    assert keys() == {}, keys()
    send([0x93] + sum(([48 + i, 90] for i in range(9)), []))         # 9 keys, 8 voices: the oldest (48) is stolen
    k = keys()
    assert sorted(k) == list(range(49, 57)), sorted(k)
    y = sync()
    amps2 = [tone(y, n) for n in range(49, 57)]
    assert all(a > 0.005 for a in amps2), amps2
    send([0xB0, 120, 0])
    assert keys() == {}, keys()
    return dict(chord=" ".join("%.3f" % a for a in amps), off_pitch=round(off, 4), steal8=" ".join("%.3f" % a for a in amps2))


def emu_jog_test(img, model, sy):
    """The rocker (FDE8) and the encoder (FDE9) apart: a flick moves one row, held part way rows keep moving, held all
    the way the page flips to the parent menu's next / previous SUB page (and again while held), the encoder edits a
    value row without EDIT."""
    e = new_emu(img, model); e.run(1_500_000)
    page = lambda: e.ram[0x7010] << 8 | e.ram[0x7011]
    sel, edit, depth = (lambda: e.ram[0x7012]), (lambda: e.ram[0x7014]), (lambda: e.ram[0x7015])
    def items(pg):
        return [(img[pg + 8 + 8 * i], img[pg + 10 + 8 * i] << 8 | img[pg + 11 + 8 * i]) for i in range(img[pg + 4])]
    root = items(sy["pg_root"]); subs = [i for i, (k, _) in enumerate(root) if k == 0]
    assert page() == sy["pg_root"] and sel() == 0, (hex(page()), sel())
    e.rock(1); e.rock(1); assert sel() == 2, "flicks down: sel %d" % sel()
    e.rock(-1); assert sel() == 1, "flick up: sel %d" % sel()
    e.rock(5, hold_steps=1_400_000); scrolled = sel()
    assert scrolled >= min(len(root) - 1, 4), "part way held: sel %d" % scrolled
    for _ in range(len(root)): e.rock(-1)
    assert sel() == 0
    row = subs[1]
    for _ in range(row): e.rock(1)
    e.press("EDIT")
    assert depth() == 1 and page() == root[row][1], "EDIT did not open row %d" % row
    k, var = items(page())[0][0], None
    pg = page(); vi = next(i for i, (kk, _) in enumerate(items(pg)) if kk in (1, 2, 5))
    for _ in range(vi): e.rock(1)
    ptr = img[pg + 10 + 8 * vi] << 8 | img[pg + 11 + 8 * vi]
    v0 = e.ram[ptr & 0x7FFF]
    e.dial(-1 if v0 > 0 else 1, 150000)
    assert edit() == 1 and e.ram[ptr & 0x7FFF] != v0, "encoder did not edit row %d (%d -> %d)" % (vi, v0, e.ram[ptr & 0x7FFF])
    e.press("EXIT")
    nxt = subs[(subs.index(row) + 1) % len(subs)]; nxt2 = subs[(subs.index(row) + 2) % len(subs)]
    e.rock(10, hold_steps=560_000)
    assert page() == root[nxt][1] and depth() == 1 and e.ram[0x7018 + 2] == nxt, \
        "full hold down: page %04X (want %04X), parent row %d" % (page(), root[nxt][1], e.ram[0x701A])
    e.rock(-10, hold_steps=560_000)
    assert page() == root[row][1], "full hold up: back to %04X, got %04X" % (root[row][1], page())
    e.rock(10, hold_steps=1_300_000)
    assert page() == root[nxt2][1], "held on: two pages on, %04X want %04X" % (page(), root[nxt2][1])
    return dict(scrolled_to=scrolled, flipped="%04X -> %04X -> %04X" % (root[row][1], root[nxt][1], root[nxt2][1]))


def emu_link_test(img, model, sy):
    """The MIDI link (tools/keyslink.py) from the ROSETTA home screen: CC 119 opens the page, a CC sets VOLUME, SysEx
    uploads a marked program + coefficient image and loads it (byte-exact at the TMS57002), pokes one coefficient,
    goes back to the ROM image, CC 119 leaves the page."""
    import dsplog, keysdsp, keyslink
    im = keysdsp.build(platform=model)
    e = new_emu(img, model); e.run(1_500_000)
    def page(): return e.ram[0x7010] << 8 | e.ram[0x7011]
    assert page() == sy["pg_root"], "not on the home screen"
    e.dsplog = []
    def send(bs, steps=400000):
        e.midi_in += list(bs); e.run(len(bs) * 600 + steps)
    send(keyslink.cc_page(True), 1_500_000)
    assert page() == sy["pg_keys"] and e.ram[0x70A0] == 1, "CC 119 did not open the page (page %04X own %d)" % (page(), e.ram[0x70A0])
    d0, _ = dsplog.decode(e.dsplog)
    assert d0.loads >= 1 and d0.prog[:len(im.prog)] == im.prog, "page open: our program not loaded"
    n0 = len(d0.updates)
    send(keyslink.cc_set("VOLUME", 40))
    assert e.ram[0x7080 + 16] == 40, "CC VOLUME -> K_SET %d" % e.ram[0x7080 + 16]
    d1, _ = dsplog.decode(e.dsplog)
    vin = set(im.addrs("VINL") + im.addrs("VINR"))
    assert any(a in vin for a, v in d1.updates[n0:]), "VOLUME not sent to VIN"
    # a marked image: our program with its last cmem word changed
    cm = list(im.cmem); cm[255] = 0x13572468
    mark = len(e.dsplog)
    for m in keyslink.sx_upload(im.prog, cm): send(m, 20000)
    send(keyslink.sx_load(), 1_500_000)
    d2, _ = dsplog.decode(e.dsplog[mark:])
    assert d2.loads == 1, "link load: %d program loads" % d2.loads
    assert d2.prog[:len(im.prog)] == im.prog and d2.cmem.get(255) == 0x13572468 and \
        all(d2.cmem.get(i, 0) == cm[i] for i in range(256)), "link image differs at the TMS57002"
    mark = len(e.dsplog)
    send(keyslink.sx_coef(0x85, 0xDEADBEEF))
    d3, _ = dsplog.decode(e.dsplog[mark:])
    assert (0x85, 0xDEADBEEF) in d3.updates, "poke: %s" % d3.updates[:4]
    mark = len(e.dsplog)
    send(keyslink.sx_rom(), 1_500_000)
    d4, _ = dsplog.decode(e.dsplog[mark:])
    assert d4.loads == 1 and all(d4.cmem.get(i, 0) == im.cmem[i] for i in range(256)), "ROM image not back"
    send(keyslink.cc_page(False), 1_500_000)
    assert page() == sy["pg_root"] and e.ram[0x70A0] == 0, "CC 119 0 did not leave the page"
    return dict(upload_msgs=len(keyslink.sx_upload(im.prog, cm)), poke="ok", rom_back="ok")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "gp5"
    out = os.path.join(ROOT, "build", PLATFORMS[model]["out"])
    img, sy = build(out, model)
    shots, ram = emu_tour(bytes(img), os.path.join(ROOT, "out", "shots" if model == "gp5" else "shots_" + model), model)
    for name, lines in shots:
        print("--", name)
    print("power-on with HEX GIRL off, top text row after 0.27 s:", repr(emu_boot_skip(bytes(img), ram, model)))
    print("boots into ROSETTA, EXIT stays:", emu_boots_into_rosetta(bytes(img), model, sy))
    print("rocker / encoder:", emu_jog_test(bytes(img), model, sy))
    lv, ups = emu_input_test(bytes(img), model)
    print("INPUT page: ADC L/R segments", lv, " c(05) sent:", " ".join("%02X" % u for u in ups))
    if "keys" in PLATFORMS[model]:
        print("DDS-DSP SAMPLER:", emu_keys_test(bytes(img), model, sy))
        print("MIDI IN:", emu_midi_test(bytes(img), model, sy))
        print("MIDI link:", emu_link_test(bytes(img), model, sy))
    print("x4 md5", hashlib.md5(open(out[:-4] + "_SST39SF040_x4.bin", "rb").read()).hexdigest())
