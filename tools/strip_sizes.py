# How many ROM bytes would each stock feature free? Code reachable ONLY through a feature's entry points
# (trace everything, then trace again with the feature's entries blocked; the difference is exclusive to it).
#   python -I tools/strip_sizes.py dumps/hrgp5_eprom_read1.bin
import sys, os, struct
sys.path.insert(0, os.path.dirname(__file__))
import gp5re

FEATURES = {
    "TEST mode (A+SYSTEM at power on)": [0x05488],
    "SYSTEM:Memory (Copy/Move/Exchange/Delete/Protect)": [0x04887, 0x0488A],
    "SYSTEM:Clock": [0x049D0],
    "SAVE (Destination + Name Edit)": [0x03198],
    "METER/TUNE (tuner + meter)": [0x00960],
    "EDIT screens": [0x0AD24, 0x0AF00, 0x0AFBF],
    "settings page engine (SYSTEM Set Up/MIDI/Pedal, EDIT Level/RTC)": [0x036AC, 0x036C5],
}


def covered(rom, blocked):
    t = gp5re.Tracer(rom)
    t.seen |= set()
    run = t.run
    def run_blocked(entries):
        return run([e for e in entries if e not in blocked])
    t.run = run_blocked
    orig_dec = t.d.dec
    def dec(pc):
        i = orig_dec(pc)
        if i.target in blocked: i.target = None
        return i
    t.d.dec = dec
    vec = [struct.unpack(">I", rom[v:v + 4])[0] for v in range(0, 0x100, 4)]
    roots = {a for a in vec if 0x180 <= a < len(rom)} | {a for a in gp5re.SYMS if a not in blocked}
    t.run(sorted(roots))
    while True:
        new = [h for h in t.find_tables() if h not in t.seen and h not in blocked]
        if not new: break
        t.run(new)
    return {b for pc, i in t.code.items() for b in range(pc, pc + i.len)}


if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    full = covered(rom, set())
    print("traced code: %d B" % len(full))
    for name, entries in FEATURES.items():
        rest = covered(rom, set(entries))
        only = full - rest
        lo, hi = (min(only), max(only)) if only else (0, 0)
        print("%-62s %5d B code only it uses   (%05X-%05X)" % (name, len(only), lo, hi))
    print("%-62s %5d B data (176 B per preset)" % ("factory presets 0-99 (0xB318-0xF7D7)", 100 * 0xB0))
