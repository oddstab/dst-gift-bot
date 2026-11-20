"""從本機 DST 安裝產生造型圖，完全不連網。

格式逆向結論（2026-11，build 762172）：

`.dyn`（`data/anim/dynamic/<prefab>.dyn`）
    不是壓縮檔，而是把一個 ZIP 以 8 bytes 為單位做「重排 + XOR」混淆。
    解鎖公式（來自 dontstarve_steam_x64.exe 的 LoadDynamicTextures）：

        out[i] = block[src[i]] ^ key[i]
        src = [5, 3, 6, 7, 4, 2, 0, 1]
        key = [0x8d, 0x8e, 0x8f, 0x90, 0x91, 0x92, 0x93, 0x94]

    解出來是含 `atlas-0.tex` 的 ZIP，裡面就是該造型的貼圖。

KTEX
    `KTEX` magic + 140 bytes 檔頭 + BC3(DXT5) 區塊，含 mip chain。
    寬高是 little-endian u16，分別在 offset 8 與 10。

裁切
    圖集是一張放滿零件的大圖，取 alpha 連通區塊中最大的一塊當代表圖。
"""
import io
import struct
import zipfile
import zlib

DYN_SRC = (5, 3, 6, 7, 4, 2, 0, 1)
DYN_KEY = (0x8D, 0x8E, 0x8F, 0x90, 0x91, 0x92, 0x93, 0x94)
KTEX_MAGIC = b"KTEX"
KTEX_HEADER = 140          # KTEX magic 之後的固定檔頭長度（含 8 級 mip 表）
ALPHA_THRESHOLD = 24


def decrypt_dyn(raw):
    """把 .dyn 的混淆還原成 ZIP bytes。

    遊戲的迴圈是 `edi = 8; do { 處理一個區塊; edi += 8 } while (edi < size)`，
    所以**最後一個 8 bytes 區塊永遠不動**，不管檔案長度是不是 8 的倍數。
    用 (len-1)//8 才符合：長度 8n 時只處理 n-1 塊，8n+r 時處理 n 塊。
    """
    if raw[:2] == b"PK":            # 有些 .dyn 本來就是明文 ZIP，引擎也會跳過轉換
        return bytes(raw)
    out = bytearray(raw)
    for base in range(0, (len(raw) - 1) // 8 * 8, 8):
        block = raw[base:base + 8]
        for i in range(8):
            out[base + i] = block[DYN_SRC[i]] ^ DYN_KEY[i]
    return bytes(out)


def read_atlas(dyn_bytes):
    """從解開的 ZIP 取出 atlas-0.tex。"""
    with zipfile.ZipFile(io.BytesIO(dyn_bytes)) as z:
        return z.read("atlas-0.tex")


def ktex_size(tex):
    if tex[:4] != KTEX_MAGIC:
        raise ValueError("not a KTEX file")
    return struct.unpack_from("<HH", tex, 8)


def _bc3_block(block, out, w, bx, by):
    a0, a1 = block[0], block[1]
    abits = int.from_bytes(block[2:8], "little")
    c0, c1 = struct.unpack_from("<HH", block, 8)
    cbits = int.from_bytes(block[12:16], "little")

    alphas = [a0, a1]
    if a0 > a1:
        alphas += [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        alphas += [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]

    ca = (((c0 >> 11) & 0x1F) * 255 // 31, ((c0 >> 5) & 0x3F) * 255 // 63,
          (c0 & 0x1F) * 255 // 31)
    cb = (((c1 >> 11) & 0x1F) * 255 // 31, ((c1 >> 5) & 0x3F) * 255 // 63,
          (c1 & 0x1F) * 255 // 31)
    cols = (ca, cb,
            tuple((2 * a + b) // 3 for a, b in zip(ca, cb)),
            tuple((a + 2 * b) // 3 for a, b in zip(ca, cb)))

    for i in range(16):
        r, g, b = cols[(cbits >> (2 * i)) & 3]
        x, y = bx * 4 + (i % 4), by * 4 + (i // 4)
        o = (y * w + x) * 4
        out[o:o + 4] = bytes((r, g, b, alphas[(abits >> (3 * i)) & 7]))


def ktex_to_rgba(tex):
    """解出 (寬, 高, RGBA bytes)。只取第一級 mip。"""
    w, h = ktex_size(tex)
    need = KTEX_HEADER + ((w + 3) // 4) * ((h + 3) // 4) * 16
    if len(tex) < need:
        raise ValueError(f"KTEX truncated: {len(tex)} < {need}")
    rgba = bytearray(w * h * 4)
    pos = KTEX_HEADER
    for by in range(h // 4):
        for bx in range(w // 4):
            _bc3_block(tex[pos:pos + 16], rgba, w, bx, by)
            pos += 16
    return w, h, bytes(rgba)


def largest_region(rgba, w, h, threshold=ALPHA_THRESHOLD):
    """回傳 alpha 最大連通區塊的 bbox (x0, y0, x1, y1)，找不到回 None。"""
    seen = bytearray(w * h)
    best = None
    for sy in range(h):
        for sx in range(w):
            idx = sy * w + sx
            if seen[idx] or rgba[idx * 4 + 3] <= threshold:
                continue
            stack = [(sx, sy)]
            seen[idx] = 1
            area = 0
            x0, y0, x1, y1 = sx, sy, sx, sy
            while stack:
                x, y = stack.pop()
                area += 1
                if x < x0:
                    x0 = x
                elif x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                elif y > y1:
                    y1 = y
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        n = ny * w + nx
                        if not seen[n] and rgba[n * 4 + 3] > threshold:
                            seen[n] = 1
                            stack.append((nx, ny))
            if best is None or area > best[0]:
                best = (area, x0, y0, x1, y1)
    if best is None:
        return None
    return best[1:]


def crop(rgba, w, h, box, pad=4):
    """裁出 bbox 並留一圈透明邊，方便辨識。"""
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    ow, oh = cw + pad * 2, ch + pad * 2
    out = bytearray(ow * oh * 4)
    for y in range(ch):
        src = ((y0 + y) * w + x0) * 4
        dst = ((y + pad) * ow + pad) * 4
        out[dst:dst + cw * 4] = rgba[src:src + cw * 4]
    return ow, oh, bytes(out)


def encode_png(w, h, rgba):
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def opaque_ratio(rgba):
    if not rgba:
        return 0.0
    return sum(1 for i in range(3, len(rgba), 4) if rgba[i] > ALPHA_THRESHOLD) / (len(rgba) // 4)


def render_prefab(game_dir, prefab):
    """prefab -> PNG bytes；本機沒有這個造型的 .dyn 就回 None。"""
    import os
    path = os.path.join(game_dir, "data", "anim", "dynamic", prefab.lower() + ".dyn")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        raw = f.read()
    tex = read_atlas(decrypt_dyn(raw))
    w, h, rgba = ktex_to_rgba(tex)
    box = largest_region(rgba, w, h)
    if box is None:
        return None
    cw, ch, cropped = crop(rgba, w, h, box)
    return encode_png(cw, ch, cropped)
