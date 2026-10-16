# SP-808 Rosetta boot (Hex girl, 45 frames 132x32, 50 ms ticks) -> 228x40 GIF for anim_build.py
#   python -I tools/sp808_boot.py frames/rosetta_boot.gif [path/to/roland_chan_frames.npz]
# The npz is in the SP-808 Rosetta OS repo (assets/). frames/rosetta_boot.gif is already made, so this is only
# needed to redo it.
import sys, os
import numpy as np
from PIL import Image

SRC = sys.argv[2] if len(sys.argv) > 2 else \
    os.path.join(os.path.dirname(__file__), "..", "..", "sp808", "assets", "roland_chan_frames.npz")
W, H, X0, Y0 = 228, 40, 48, 4      # centred; x 48 = byte column 8 (6 px per byte)

z = np.load(SRC)
scr, ticks = z["screens"].astype(bool), [int(t) for t in z["ticks"]]
ims = []
for s in scr:
    f = np.zeros((H, W), bool)
    f[Y0:Y0 + s.shape[0], X0:X0 + s.shape[1]] = s
    ims.append(Image.fromarray(np.where(f, 0, 255).astype(np.uint8), "L"))
out = sys.argv[1]
ims[0].save(out, save_all=True, append_images=ims[1:], duration=[t * 50 for t in ticks], loop=0, optimize=False)
print("%d frames, %d ticks (%.1f s at 50 ms) -> %s" % (len(ims), sum(ticks), sum(ticks) * 0.05, out))
