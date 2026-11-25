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
    `KTEX` magic (4B) + flags u32 (4B，bit 13..17 為 mipmap 層數 N) +
    N 個 10-byte mipmap entry (`<HHHI`：width, height, pitch, size) +
    BC3(DXT5) 區塊。檔頭總長 = `8 + 10 * N`（512×512 為 108B，1024×1024 為 118B）。

裁切
    優先從 `data/databundles/anim_dynamic.zip` 內對應的 `build.bin` 讀取
    `SWAP_ICON`（SDBM hash `0xf6aadf05`）的頂點 UV 範圍，直接定位官方商品圖示；
    若無 `build.bin` 則退回取最大連通區塊。
"""
import io
import os
import struct
import zipfile
import zlib

DYN_SRC = (5, 3, 6, 7, 4, 2, 0, 1)
DYN_KEY = (0x8D, 0x8E, 0x8F, 0x90, 0x91, 0x92, 0x93, 0x94)
KTEX_MAGIC = b"KTEX"
KTEX_HEADER = 108          # 512x512 (10 mips) 的預設檔頭長度；實際由 ktex_header_size(tex) 動態計算
SWAP_ICON_HASH = 0xF6AADF05
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


def ktex_header_size(tex):
    """回傳 KTEX 檔頭長度：8 + 10 * mip_count。"""
    if tex[:4] != KTEX_MAGIC:
        raise ValueError("not a KTEX file")
    flags = struct.unpack_from("<I", tex, 4)[0]
    mips = (flags >> 13) & 0x1F
    return 8 + mips * 10


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
    hdr = ktex_header_size(tex)
    need = hdr + ((w + 3) // 4) * ((h + 3) // 4) * 16
    if len(tex) < need:
        raise ValueError(f"KTEX truncated: {len(tex)} < {need}")
    rgba = bytearray(w * h * 4)
    pos = hdr
    for by in range(h // 4):
        for bx in range(w // 4):
            _bc3_block(tex[pos:pos + 16], rgba, w, bx, by)
            pos += 16
    # KTEX 的列是 OpenGL 慣例的 bottom-up，直接照順序輸出會上下顛倒
    stride = w * 4
    flipped = bytearray()
    for y in range(h - 1, -1, -1):
        flipped += rgba[y * stride:(y + 1) * stride]
    return w, h, bytes(flipped)


def swap_icon_uv_box(build_bytes):
    """從 build.bin 找出 SWAP_ICON 的頂點 UV 範圍 (u0, v0, u1, v1)；找不到回 None。"""
    if build_bytes[:4] != b"BILD":
        return None
    _, _, nsyms, _, blen = struct.unpack_from("<4sIIII", build_bytes, 0)
    pos = 20 + blen
    natlas = struct.unpack_from("<I", build_bytes, pos)[0]
    pos += 4
    for _ in range(natlas):
        alen = struct.unpack_from("<I", build_bytes, pos)[0]
        pos += 4 + alen
    target = None
    for _ in range(nsyms):
        shash, nimg = struct.unpack_from("<II", build_bytes, pos)
        pos += 8
        for _ in range(nimg):
            fr = struct.unpack_from("<IIffffII", build_bytes, pos)
            pos += 32
            if shash == SWAP_ICON_HASH and target is None:
                target = fr
    if target is None:
        return None
    nverts = struct.unpack_from("<I", build_bytes, pos)[0]
    pos += 4
    v0, vcnt = target[6], target[7]
    if vcnt <= 0 or v0 + vcnt > nverts:
        return None
    us, vs = [], []
    for vi in range(v0, v0 + vcnt):
        _, _, _, u, v, _ = struct.unpack_from("<ffffff", build_bytes, pos + vi * 24)
        us.append(u)
        vs.append(v)
    return min(us), min(vs), max(us), max(vs)


def load_swap_icon_box(game_dir, prefab, w, h):
    """讀取 anim_dynamic.zip 內該造型的 SWAP_ICON 像素範圍 (x0, y0, x1, y1)。"""
    bundle = os.path.join(game_dir, "data", "databundles", "anim_dynamic.zip")
    if not os.path.isfile(bundle):
        return None
    entry = f"anim/dynamic/{prefab.lower()}.zip"
    try:
        with zipfile.ZipFile(bundle) as zf:
            inner = zf.read(entry)
        with zipfile.ZipFile(io.BytesIO(inner)) as iz:
            build = iz.read("build.bin")
    except Exception:
        return None
    uv = swap_icon_uv_box(build)
    if not uv:
        return None
    u0, v0, u1, v1 = uv
    x0 = max(0, min(w - 1, int(u0 * w)))
    x1 = max(x0, min(w - 1, int(round(u1 * w)) - 1))
    # 翻轉後的列座標：v=1 在頂部 (y=0)，v=0 在底部 (y=h-1)
    y0 = max(0, min(h - 1, int((1.0 - v1) * h)))
    y1 = max(y0, min(h - 1, int(round((1.0 - v0) * h)) - 1))
    return x0, y0, x1, y1


def trim_alpha(rgba, w, h, box, threshold=ALPHA_THRESHOLD):
    """在給定的 box 內收緊到實際有非透明像素的範圍。"""
    bx0, by0, bx1, by1 = box
    x0, y0, x1, y1 = bx1, by1, bx0, by0
    found = False
    for y in range(by0, by1 + 1):
        row = y * w
        for x in range(bx0, bx1 + 1):
            if rgba[(row + x) * 4 + 3] > threshold:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
                found = True
    return (x0, y0, x1, y1) if found else box


def dominant_color(rgba, step=16):
    """取樣找出最常見的顏色，當成背景色。"""
    counts = {}
    for i in range(0, len(rgba) - 3, 4 * step):
        key = rgba[i:i + 4]
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) if counts else b"\x00\x00\x00\x00"


def subject_mask(rgba, w, h, bg, threshold=ALPHA_THRESHOLD, tol=40):
    """不是背景、也不是透明的像素。"""
    br, bgc, bb, ba = bg[0], bg[1], bg[2], bg[3]
    mask = bytearray(w * h)
    for i in range(w * h):
        o = i * 4
        if rgba[o + 3] <= threshold:
            continue
        if (abs(rgba[o] - br) <= tol and abs(rgba[o + 1] - bgc) <= tol
                and abs(rgba[o + 2] - bb) <= tol and abs(rgba[o + 3] - ba) <= tol):
            continue
        mask[i] = 1
    return mask


def largest_region(mask, w, h):
    """在遮罩上找最大連通區塊，回傳 bbox (x0, y0, x1, y1)；找不到回 None。"""
    seen = bytearray(w * h)
    best = None
    for sy in range(h):
        for sx in range(w):
            idx = sy * w + sx
            if seen[idx] or not mask[idx]:
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
                        if not seen[n] and mask[n]:
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
    path = os.path.join(game_dir, "data", "anim", "dynamic", prefab.lower() + ".dyn")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        raw = f.read()
    tex = read_atlas(decrypt_dyn(raw))
    w, h, rgba = ktex_to_rgba(tex)

    box = load_swap_icon_box(game_dir, prefab, w, h)
    if box is not None:
        box = trim_alpha(rgba, w, h, box)
    else:
        bg = dominant_color(rgba)
        mask = subject_mask(rgba, w, h, bg)
        box = largest_region(mask, w, h)
    if box is None:
        return None
    cw, ch, cropped = crop(rgba, w, h, box)
    return encode_png(cw, ch, cropped)
