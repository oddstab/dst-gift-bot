"""離線造型圖解碼器的測試。需要本機安裝 DST；找不到遊戲就跳過。

跑法：python test_skinart.py
"""
import os
import struct
import sys
import unittest
import zlib

import dst_skinart as skinart


def find_game():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.environ.get("DST_DIR"), os.path.dirname(here),
                 r"C:\Program Files (x86)\Steam\steamapps\common\Don't Starve Together"):
        if cand and os.path.isdir(os.path.join(cand, "data", "anim", "dynamic")):
            return cand
    try:                                   # 交給機器人自己的安裝路徑探索
        import dst_gift_bot
        found = dst_gift_bot.find_game()
        if found and os.path.isdir(os.path.join(found, "data", "anim", "dynamic")):
            return found
    except Exception:
        pass
    return None


GAME = find_game()
SAMPLES = ("BODY_BUTTONS_BLUE_SKY", "DECOR_LAMP_FANTASY", "LEGS_WORTOX_SURVIVOR")


def decode_png(data):
    """只支援本專案寫出的 PNG（8-bit RGBA、filter 0）。回傳 (w, h, rgba)。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "PNG magic 不對"
    pos = 8
    w = h = None
    idat = b""
    while pos < len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            w, h, depth, color = struct.unpack_from(">IIBB", body, 0)
            assert depth == 8 and color == 6, f"預期 8-bit RGBA，得到 {depth}/{color}"
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 4
    rgba = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        assert f == 0, f"本解碼器只支援 filter 0，遇到 {f}"
        line = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        rgba += line
        prev = line
    return w, h, bytes(rgba)


@unittest.skipUnless(GAME, "找不到 DST 安裝，跳過造型圖測試")
class SkinArtTest(unittest.TestCase):
    def test_dyn_decrypts_to_zip(self):
        path = os.path.join(GAME, "data", "anim", "dynamic", "body_buttons_blue_sky.dyn")
        with open(path, "rb") as f:
            raw = f.read()
        plain = skinart.decrypt_dyn(raw)
        self.assertEqual(plain[:4], b"PK\x03\x04")
        tex = skinart.read_atlas(plain)
        self.assertEqual(tex[:4], b"KTEX")

    def test_ktex_size_matches_header(self):
        path = os.path.join(GAME, "data", "anim", "dynamic", "body_buttons_blue_sky.dyn")
        with open(path, "rb") as f:
            tex = skinart.read_atlas(skinart.decrypt_dyn(f.read()))
        w, h = skinart.ktex_size(tex)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        need = skinart.KTEX_HEADER + ((w + 3) // 4) * ((h + 3) // 4) * 16
        self.assertGreaterEqual(len(tex), need)

    def test_three_slots_render_non_blank_png(self):
        for prefab in SAMPLES:
            with self.subTest(prefab=prefab):
                data = skinart.render_prefab(GAME, prefab)
                self.assertIsNotNone(data, f"{prefab} 沒有產圖")
                w, h, rgba = decode_png(data)
                self.assertGreater(w, 0)
                self.assertGreater(h, 0)
                opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i] > 24)
                ratio = opaque / (w * h)
                self.assertGreater(ratio, 0.05, f"{prefab} 幾乎全透明 ({ratio:.3f})")
                colors = {(rgba[i], rgba[i + 1], rgba[i + 2])
                          for i in range(0, len(rgba), 4) if rgba[i + 3] > 24}
                self.assertGreater(len(colors), 8, f"{prefab} 顏色種類過少")

    def test_unknown_prefab_returns_none(self):
        self.assertIsNone(skinart.render_prefab(GAME, "NOT_A_REAL_SKIN_PREFAB"))

    def test_decrypt_is_bytewise_reversible_permutation(self):
        # 8 bytes 的區塊在解密後必須是原區塊的排列再 XOR，長度不變
        raw = bytes(range(64))
        out = skinart.decrypt_dyn(raw)
        self.assertEqual(len(out), len(raw))
        self.assertEqual(out[:8], bytes(
            raw[skinart.DYN_SRC[i]] ^ skinart.DYN_KEY[i] for i in range(8)))


if __name__ == "__main__":
    print("game:", GAME)
    unittest.main(verbosity=2)
