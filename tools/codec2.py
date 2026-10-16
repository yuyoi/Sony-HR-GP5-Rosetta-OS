# Frame codec v2 for the HR-GP5 boot player. Each frame is decoded in place over the previous one.
# Stream = frames, then 00.  Frame = hold byte (1-127) + tokens,
#                             or hold|80 + 2-byte page-1 address of an earlier frame's tokens (replay).
# Tokens (pos runs 0..1519 over the 38x40 graphics bytes):
#   00-1F  skip n+1 (1-32)
#   20-3E  skip (n+1)*32 (32-992)        3F = end of frame (rest unchanged)
#   40-5F  fill n+1 (1-32) with next byte
#   60-7F  copy L = ((t&1F)<<8 | b1)+1 bytes from pos+b2   (b2 = 1-255, forward only)
#   80-BF  XOR n+1 (1-64) literal bytes into the buffer
#   C0-FF  copy n+1 (1-64) bytes from pos+1
GB = 38 * 40


def _runs(prev, cur):
    n = len(cur)
    eq = [0] * (n + 1); same = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        eq[i] = eq[i + 1] + 1 if cur[i] == prev[i] else 0
        same[i] = same[i + 1] + 1 if i + 1 < n and cur[i + 1] == cur[i] else 1
    return eq, same


def _copy_runs(prev, cur, off):
    n = len(cur); r = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        r[i] = r[i + 1] + 1 if i + off < n and cur[i] == prev[i + off] else 0
    return r


OFFS = sorted(set([1, 2, 3, 4, 5, 6] + [38 * k + d for k in range(1, 7) for d in (-2, -1, 0, 1, 2)]))


def encode(prev, cur, delta_only=False):
    n = len(cur)
    eq, same = _runs(prev, cur)
    cr = {} if delta_only else {o: _copy_runs(prev, cur, o) for o in OFFS}
    INF = 1 << 30
    dp = [INF] * (n + 1); ch = [None] * (n + 1); dp[n] = 0
    for i in range(n - 1, -1, -1):
        best, bc = INF, None
        if eq[i] == n - i and i:
            best, bc = 1, ("end", n - i)
        if eq[i]:
            L = eq[i]
            for l in {min(L, 32), (min(L, 992) // 32) * 32}:
                if l:
                    c = 1 + dp[i + l]
                    if c < best: best, bc = c, ("skip", l)
        if not delta_only and same[i] >= 2:
            l = min(same[i], 32); c = 2 + dp[i + l]
            if c < best: best, bc = c, ("fill", l)
        for o, r in cr.items():
            if r[i]:
                if o == 1:
                    l = min(r[i], 64); c = 1 + dp[i + l]
                    if c < best: best, bc = c, ("copy1", l)
                if r[i] >= 3:
                    l = min(r[i], 8192); c = 3 + dp[i + l]
                    if c < best: best, bc = c, ("copy", l, o)
        for l in range(1, min(64, n - i) + 1):
            c = 1 + l + dp[i + l]
            if c < best: best, bc = c, ("xor", l)
        dp[i], ch[i] = best, bc
    out, i = bytearray(), 0
    while i < n:
        c = ch[i]
        if c[0] == "end": out.append(0x3F)
        elif c[0] == "skip": out.append(c[1] - 1 if c[1] <= 32 else 0x20 | (c[1] // 32 - 1))
        elif c[0] == "fill": out += bytes([0x40 | (c[1] - 1), cur[i]])
        elif c[0] == "copy1": out.append(0xC0 | (c[1] - 1))
        elif c[0] == "copy": l = c[1] - 1; out += bytes([0x60 | (l >> 8), l & 0xFF, c[2]])
        else: out.append(0x80 | (c[1] - 1)); out += bytes(a ^ b for a, b in zip(cur[i:i + c[1]], prev[i:i + c[1]]))
        i += c[1]
    return bytes(out)


def decode_frame(buf, s, p):
    """apply one frame's tokens to buf in place; returns the stream pos after the frame"""
    i, n = 0, len(buf)
    while i < n:
        t = s[p]; p += 1
        if t >= 0xC0:
            for k in range((t & 0x3F) + 1): buf[i] = buf[i + 1]; i += 1
        elif t >= 0x80:
            for k in range((t & 0x3F) + 1): buf[i] ^= s[p]; p += 1; i += 1
        elif t >= 0x60:
            L = (((t & 0x1F) << 8) | s[p]) + 1; o = s[p + 1]; p += 2
            for k in range(L): buf[i] = buf[i + o]; i += 1
        elif t >= 0x40:
            L = (t & 0x1F) + 1; v = s[p]; p += 1
            for k in range(L): buf[i] = v; i += 1
        elif t == 0x3F: i = n
        elif t >= 0x20: i += ((t & 0x1F) + 1) * 32
        else: i += t + 1
    assert i == n, "overrun %d" % i
    return p


def build_stream(frames, holds, base):
    """frames: list of 1520-byte frames. base = page-1 address the stream will sit at.
    -> stream bytes (replays point at earlier frames with identical effect)."""
    prev = bytes(GB)
    out, starts = bytearray(), []        # starts: (addr of tokens, tokens)
    xd = [bytes(a ^ b for a, b in zip(p, f)) for p, f in zip([bytes(GB)] + frames[:-1], frames)]
    rep = {d for d in xd if xd.count(d) > 1}
    for f, h, d in zip(frames, holds, xd):
        assert 1 <= h <= 127
        reuse = None
        for addr, toks in starts:
            b = bytearray(prev)
            try: decode_frame(b, toks + b"\x3f" * 2, 0)
            except (AssertionError, IndexError): continue
            if bytes(b) == f: reuse = addr; break
        if reuse is not None:
            out += bytes([0x80 | h, (reuse >> 8) & 0xFF, reuse & 0xFF])
        else:
            toks = encode(prev, f, delta_only=d in rep)
            out.append(h)
            starts.append(((base + len(out)) & 0xFFFF, toks))
            out += toks
        prev = f
    out.append(0)
    return bytes(out)


def decode_stream(s, base):
    """-> [(hold, frame)]; base = page-1 address of s"""
    buf, p, res = bytearray(GB), 0, []
    while s[p]:
        h = s[p]; p += 1
        if h & 0x80:
            a = ((s[p] << 8) | s[p + 1]) - (base & 0xFFFF); p += 2
            decode_frame(buf, s, a); h &= 0x7F
        else:
            p = decode_frame(buf, s, p)
        res.append((h, bytes(buf)))
    return res
