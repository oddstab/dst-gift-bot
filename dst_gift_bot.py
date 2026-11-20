"""Don't Starve Together gift bot — run in the background with no game window.

Mechanism (reverse-engineered and verified on build 762172, see REPORT.md):
  1. steam_api64.dll + steam_appid.txt(322330) -> Steam auth session ticket
  2. POST https://login.kleientertainment.com/login/LoginViaSteam -> Klei token
  3. every 360 s POST /clientitems/DST/Tick  {"Token": ...}
     <- this is the heartbeat the game itself sends; it is what accrues drop time
  4. POST /clientitems/DST/DailyGift {"ClientToken": ...}   <- daily gift
  5. POST /clientitems/DST/GetAllItems                      <- confirm items arrived

Usage:
  python dst_gift_bot.py --once        # single cycle, prints result
  python dst_gift_bot.py               # daemon, one heartbeat every 6 minutes
  python dst_gift_bot.py --selftest
  python dst_gift_bot.py --game "D:\\Steam\\steamapps\\common\\Don't Starve Together"
"""
import argparse
import binascii
import ctypes
import datetime
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

import dst_skinart

BASE = os.path.dirname(os.path.abspath(__file__))
TICK_INTERVAL = 360                    # matches the float constant at 0x1404f3390 in the client
DAILY_CHECK_INTERVAL = 3600
INVENTORY_CHECK_EVERY = 6              # every 6 heartbeats (36 min)
STATE_PATH = os.path.join(BASE, "state.json")
LOG_PATH = os.path.join(BASE, "dst_gift_bot.log")
LOCK_PATH = os.path.join(BASE, "dst_gift_bot.lock")
CONFIG_PATH = os.path.join(BASE, "config.json")
TG_API = os.environ.get("DST_TELEGRAM_API_BASE", "https://api.telegram.org")
WEEK_ANCHOR = 1767303600        # 已知的週四 21:40 UTC，用來推算每週重置邊界
CONFIG = {}
SKIN_NAMES = {}                 # prefab -> 英文名（遊戲自己的 skin_strings.lua）
SKIN_ZH = {}                    # 英文名 -> 繁中名（chinese_t.po）
ICON_CACHE_PATH = os.path.join(BASE, "icons.json")
# 解碼器一改版就換目錄名，避免吃到舊的快取圖（例：v2 = 修正上下顛倒之後）
ICON_DIR = os.path.join(BASE, "icons_v2")
ICON_CACHE = {}                 # PREFAB -> 本機產生的 PNG 路徑

SLOT_EMOJI = {"BODY": "👕", "LEGS": "👖", "FEET": "👟", "HAND": "🧤", "HEAD": "🎩",
              "DECOR": "🪑", "PROFILEFLAIR": "✨", "PROFILEICON": "🖼", "EMOTE": "💃",
              "PORTRAIT": "🖼", "MODE": "🎭", "GRAVESTONE": "🪦"}


def load_skin_names():
    """從遊戲自己的字串表取造型名稱，離線、確定、不用猜圖片網址。"""
    global SKIN_NAMES, SKIN_ZH
    try:
        import zipfile
        path = os.path.join(GAME_DIR, "data", "databundles", "scripts.zip")
        with zipfile.ZipFile(path) as z:
            lua = z.read("scripts/skin_strings.lua").decode("utf-8", "replace")
            block = re.search(r"STRINGS\.SKIN_NAMES\s*=\s*\{(.*?)\n\}", lua, re.S)
            SKIN_NAMES = dict(re.findall(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"', block.group(1)))
            for po_name in ("chinese_t.po", "chinese_s.po"):
                try:
                    po = z.read(f"scripts/languages/{po_name}").decode("utf-8", "replace")
                except KeyError:
                    continue
                SKIN_ZH = dict(re.findall(
                    r'msgid "((?:[^"\\]|\\.)*)"\s*\nmsgstr "((?:[^"\\]|\\.)*)"', po))
                break
        log(f"skin names loaded: {len(SKIN_NAMES)} prefabs, {len(SKIN_ZH)} translations")
    except Exception as e:
        log(f"could not read skin names from the game: {e}")


def _stem(token):
    for suffix in ("ies", "es", "s", "ed", "ing"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


COLOR_WORDS = {
    "red", "crimson", "orange", "amber", "yellow", "gold", "golden", "green", "lime",
    "olive", "teal", "cyan", "blue", "navy", "indigo", "purple", "violet", "magenta",
    "pink", "rose", "white", "ivory", "cream", "black", "grey", "gray", "silver",
    "brown", "tan", "beige", "ruby", "sapphire", "emerald", "jade", "amethyst",
    "topaz", "jet", "pearl", "pure", "dark", "light", "pale", "snow", "ash", "mint",
}
NOISE_WORDS = {"basic", "none", "default", "set"}


def variant_suffix(prefab):
    """把 prefab 裡「不是造型本名」的部分抓出來，當成顏色後綴。

    BODY_BUTTONS_BLUE_SKY + "Buttoned Shirt" -> "Blue Sky"
    同一個名稱常對應多種顏色，不接後綴在 Telegram 上會看起來一模一樣。
    只在能確定是顏色時才接，接錯比不接更糟。
    """
    name = SKIN_NAMES.get(prefab.lower())
    if not name:
        return ""
    parts = prefab.split("_")
    rest = [w for w in parts[1:] if w.lower() not in NOISE_WORDS] if len(parts) > 1 else []
    stems = {_stem(t) for t in re.split(r"[^a-z0-9]+", name.lower()) if t}

    leftover, matched = [], False
    for token in rest:
        s = _stem(token.lower())
        if any(s and st and (s in st or st in s) for st in stems):
            matched = True
            continue
        leftover.append(token)

    if matched and leftover:
        return " ".join(w.capitalize() for w in leftover)

    # 本名對不上（Swimming Costume vs SWIMSUIT）或名稱已含全部資訊，
    # 這時只取最後兩段，而且必須是顏色詞才算數
    tail = rest[-2:]
    if len(tail) == 2 and all(w.lower() in COLOR_WORDS for w in tail):
        return " ".join(w.capitalize() for w in tail)
    return ""


def skin_label(item_type):
    """回傳帶圖示的可讀名稱，例如 '👕 紐扣襯衫'。"""
    if not item_type:
        return "🎁 未知造型"
    key = item_type.lower()
    en = SKIN_NAMES.get(key) or item_type.replace("_", " ").title()
    name = SKIN_ZH.get(en, en)
    suffix = variant_suffix(item_type) if key in SKIN_NAMES else ""
    emoji = SLOT_EMOJI.get(item_type.split("_")[0], "🎁")
    return f"{emoji} {name} · {suffix}" if suffix else f"{emoji} {name}"


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_icons():
    global ICON_CACHE
    try:
        with open(ICON_CACHE_PATH, encoding="utf-8") as f:
            ICON_CACHE = json.load(f)
    except Exception:
        ICON_CACHE = {}


def save_icons():
    with open(ICON_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(ICON_CACHE, f, ensure_ascii=False, indent=1)


def resolve_icon(prefab):
    """用本機遊戲的 .dyn 現地合成造型圖，回傳 PNG 檔路徑；沒有就回 None。

    格式細節見 dst_skinart.py 與 REPORT.md；全程不連任何外部網站。
    """
    if not prefab:
        return None
    key = prefab.upper()
    cached = ICON_CACHE.get(key)
    if cached and os.path.isfile(cached):
        return cached
    try:
        data = dst_skinart.render_prefab(GAME_DIR, prefab)
    except Exception as e:
        log(f"skinart failed for {prefab}: {e}")
        return None
    if not data:
        return None
    os.makedirs(ICON_DIR, exist_ok=True)
    path = os.path.join(ICON_DIR, prefab.lower() + ".png")
    with open(path, "wb") as f:
        f.write(data)
    ICON_CACHE[key] = path
    save_icons()
    return path

ITEMS_URL = "https://items.kleientertainment.com/clientitems/DST/"
LOGIN_URL = "https://login.kleientertainment.com/login/LoginViaSteam"

GAME_DIR = None


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)


def notify(plain, html=None, photo=None):
    """可選的推播。Discord 吃純文字、Telegram 吃 HTML（有圖就送圖）。"""
    try:
        url = CONFIG.get("discord_webhook") or os.environ.get("DST_DISCORD_WEBHOOK")
        if url:
            _post_json(url, {"content": plain})
        tg = CONFIG.get("telegram") or {}
        tok = tg.get("token") or os.environ.get("DST_TELEGRAM_TOKEN")
        chat = tg.get("chat_id") or os.environ.get("DST_TELEGRAM_CHAT_ID")
        if not (tok and chat):
            return
        if photo and html:
            try:
                if os.path.isfile(photo):       # 本機合成的圖，走 multipart 上傳
                    return _post_multipart(f"{TG_API}/bot{tok}/sendPhoto", "photo", photo,
                                           {"chat_id": chat, "caption": html,
                                            "parse_mode": "HTML"})
                _post_json(f"{TG_API}/bot{tok}/sendPhoto",   # 遠端 URL（備用圖源）
                           {"chat_id": chat, "photo": photo, "caption": html,
                            "parse_mode": "HTML"})
                return
            except Exception as e:
                log(f"photo send failed, falling back to text: {e}")
        payload = {"chat_id": chat, "text": html or plain, "disable_web_page_preview": True}
        if html:
            payload["parse_mode"] = "HTML"
        _post_json(f"{TG_API}/bot{tok}/sendMessage", payload)
    except Exception as e:
        log(f"notify failed: {e}")


def _post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=10).read()


def _post_multipart(url, field, file_path, extra=None, timeout=30):
    """把本機檔案用 multipart/form-data 上傳（標準函式庫，沒有額外依賴）。"""
    boundary = "----dstgift" + binascii.hexlify(os.urandom(8)).decode()
    body = io.BytesIO()
    for key, value in (extra or {}).items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.write(str(value).encode())
        body.write(b"\r\n")
    body.write(f"--{boundary}\r\n".encode())
    body.write((f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{os.path.basename(file_path)}"\r\n').encode())
    body.write(b"Content-Type: image/png\r\n\r\n")
    with open(file_path, "rb") as f:
        body.write(f.read())
    body.write(f"\r\n--{boundary}--\r\n".encode())
    data = body.getvalue()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


IS_WIN = sys.platform.startswith("win")
GAME_NAME = "Don't Starve Together"


def lib_candidates(game):
    """Where the Steamworks client library lives inside the game folder.

    Windows ships bin64/steam_api64.dll; the native Linux build ships a libsteam_api.so
    somewhere under the game root, and the exact folder has changed between builds.
    """
    if IS_WIN:
        return [os.path.join(game, "bin64", "steam_api64.dll")]
    return [os.path.join(game, "lib64", "libsteam_api.so"),
            os.path.join(game, "bin", "libsteam_api.so"),
            os.path.join(game, "lib", "libsteam_api.so"),
            os.path.join(game, "libsteam_api.so")]


def find_lib(game, explicit=None):
    for c in ([explicit] if explicit else []) + lib_candidates(game):
        if c and os.path.isfile(c):
            return c
    return None


def _steam_roots():
    """Every Steam library root we can discover on this OS."""
    roots = []
    if IS_WIN:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            roots.append(winreg.QueryValueEx(key, "SteamPath")[0])
        except Exception:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, ".steam", "steam"),
                  os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, "snap", "steam", "common", ".local", "share", "Steam")]
    for r in list(roots):
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            roots += re.findall(r'"path"\s+"([^"]+)"', _read(vdf))
    return roots


def find_game(explicit=None):
    """Locate the DST install. Works no matter where this script lives."""
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("DST_DIR"):
        cands.append(os.environ["DST_DIR"])
    cands.append(os.path.dirname(BASE))                     # script dropped inside the game folder
    for r in _steam_roots():
        r = r.replace("\\\\", "\\") if IS_WIN else r
        cands.append(os.path.join(r, "steamapps", "common", GAME_NAME))
    for c in cands:
        if c and find_lib(c):
            return os.path.abspath(c)
    return None


def build_version():
    # The User-Agent must carry the real build number or every call returns E_INVALID_TOKEN
    return _read(os.path.join(GAME_DIR, "version.txt")).strip()


class Steam:
    """Mints an auth session ticket through the Steamworks library; the game is never started."""

    def __init__(self, lib=None):
        path = lib or find_lib(GAME_DIR)
        if not path:
            raise RuntimeError(
                f"Steamworks library not found under {GAME_DIR}. "
                "Install DST for this OS, or point --game at the right folder.")
        work = os.path.join(BASE, "steamctx")
        os.makedirs(work, exist_ok=True)
        with open(os.path.join(work, "steam_appid.txt"), "w", encoding="utf-8") as f:
            f.write("322330")
        os.chdir(work)                      # SteamAPI_Init reads steam_appid.txt from cwd
        self.api = ctypes.WinDLL(path) if IS_WIN else ctypes.CDLL(path)
        self.api.SteamAPI_Init.restype = ctypes.c_bool
        if not self.api.SteamAPI_Init():
            raise RuntimeError(
                "SteamAPI_Init failed. Start the Steam client, make sure this account owns "
                "Don't Starve Together, then try again.")
        self.api.SteamAPI_RunCallbacks.restype = None
        self.api.SteamAPI_SteamUser_v021.restype = ctypes.c_void_p
        self.user = self.api.SteamAPI_SteamUser_v021()
        self._get = self.api.SteamAPI_ISteamUser_GetAuthSessionTicket
        self._get.restype = ctypes.c_uint32
        self._get.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self._cancel = self.api.SteamAPI_ISteamUser_CancelAuthTicket
        self._cancel.restype = ctypes.c_int
        self._cancel.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.handle = 0

    def steam_id(self):
        fn = self.api.SteamAPI_ISteamUser_GetSteamID
        fn.restype = ctypes.c_uint64
        fn.argtypes = [ctypes.c_void_p]
        return fn(self.user)

    def ticket_hex(self):
        # Release the previous handle, otherwise a long run eats Steam's ticket quota
        if self.handle:
            self._cancel(self.user, self.handle)
            self.handle = 0
        buf = ctypes.create_string_buffer(4096)
        size = ctypes.c_uint32(0)
        self.handle = self._get(self.user, buf, 4096, ctypes.byref(size), None)
        return binascii.hexlify(buf.raw[:size.value]).decode().upper()

    def keepalive(self):
        self.api.SteamAPI_RunCallbacks()


class Klei:
    def __init__(self, steam):
        self.steam = steam
        self.build = build_version()
        self.headers = {
            "User-Agent": f"DontStarveTogether/{self.build}",
            "Accept-Encoding": "deflate, gzip",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "charsets": "utf-8",
        }
        self.token = None
        self.user_id = None

    def _post(self, url, payload, timeout=30):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
        for k, v in self.headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                return e.code, {"Error": True, "ErrorCode": f"HTTP_{e.code}"}

    def login(self):
        status, res = self._post(LOGIN_URL, {
            "SteamTicket": self.steam.ticket_hex(),
            "appid": "322330",
            "Game": "DontStarveTogether",
        })
        if res.get("Error") or not res.get("Token"):
            raise RuntimeError(f"Klei login failed ({status}) {res.get('ErrorCode')}")
        self.token = res["Token"]
        self.user_id = res["UserID"]
        log(f"logged in as {self.user_id} (build {self.build})")

    def tick(self):
        # The field really is named Token here; DailyGift/GetAllItems use ClientToken
        return self._post(ITEMS_URL + "Tick", {"Token": self.token})[1]

    def daily_gift(self):
        return self._post(ITEMS_URL + "DailyGift", {"ClientToken": self.token})[1]

    def inventory(self):
        _, res = self._post(ITEMS_URL + "GetAllItems", {
            "ClientToken": self.token,
            "InventoryStateToken": "",
            "CheckTwitchEntitlements": True,
        })
        return res.get("Items", []) if not res.get("Error") else None

    def open_gift(self, item_id):
        # 欄位名是大寫 ItemID；送小寫 item_id 會被忽略並回 ItemID 0，看起來像成功但什麼都沒做
        return self._post(ITEMS_URL + "SetItemOpened",
                          {"ClientToken": self.token, "ItemID": item_id})[1]


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"ticks": 0, "gifts": [], "last_tick": 0, "item_count": 0}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def record_gifts(state, result, source):
    if not result.get("GiftReceived"):
        return False
    gift = {"time": int(time.time()), "source": source, "key": result.get("Key"),
            "item": result.get("ItemType")}
    state["gifts"].append(gift)
    label = gift_label(gift)
    log(f"GIFT RECEIVED via {source}: {gift.get('item') or 'no item info'}"
        f"  ({label}, week {week_count(state)}/8)")
    notify(f"GIFT {label}",
           f"<b>{esc(label)}</b>\n本週 {week_count(state)}/8 · "
           f"累積 {human_duration(state['ticks'] * TICK_INTERVAL)}",
           photo=resolve_icon(gift.get("item")))
    return True


def human_duration(seconds):
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d {h:02d}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def local_time(epoch, with_weekday=False):
    fmt = "%a %Y-%m-%d %H:%M" if with_weekday else "%Y-%m-%d %H:%M"
    return datetime.datetime.fromtimestamp(epoch).strftime(fmt)


def week_counts(state):
    """(計時掉落, 每日禮物, 本週全部)。8 件上限只算計時掉落，每日禮物是額外的。"""
    start, _ = week_bounds()
    w = [g for g in state["gifts"] if g["time"] >= start]
    return (sum(1 for g in w if g["source"] == "tick"),
            sum(1 for g in w if g["source"] == "daily"), w)


def week_count(state):
    return week_counts(state)[0]


def week_bounds(now=None):
    """目前這一週的起訖時間（週四 21:40 UTC 重置）。"""
    now = int(now or time.time())
    start = now - ((now - WEEK_ANCHOR) % (7 * 86400))
    return start, start + 7 * 86400


def stats(state):
    start, end = week_bounds()
    now = int(time.time())
    timed, daily, weekly = week_counts(state)
    print(f"week      {local_time(start)} -> {local_time(end)}  (local)")
    print(f"drops     {timed}/8 timed + {daily} daily")
    print(f"reset in  {human_duration(end - now)}  ({local_time(end, True)} local)")
    print(f"total     {len(state['gifts'])} gifts, {state['ticks']} heartbeats "
          f"({human_duration(state['ticks'] * TICK_INTERVAL)}), {state['item_count']} items")
    if weekly:
        print("recent:")
        for g in weekly[-5:]:
            print(f"  {local_time(g['time'])}  {g.get('item') or g.get('key')}  [{g['source']}]")


def progress_bar(value, total=8):
    filled = max(0, min(total, value))
    return "█" * filled + "░" * (total - filled)


def gift_label(gift):
    """每日禮物也會帶造型名，所以要用來源標記才分得出來。"""
    if not gift.get("item"):
        return "🎁 每日禮物"
    label = skin_label(gift["item"])
    return f"{label}（每日禮物）" if gift.get("source") == "daily" else label


def render_progress(state):
    _, end = week_bounds()
    now = int(time.time())
    timed, daily, weekly = week_counts(state)
    lines = [
        "🎁 <b>DST 掉落進度</b>",
        f"<code>{progress_bar(timed)}</code> <b>{timed}</b>/8 計時掉落",
        "",
        f"每日禮物 <b>{daily}</b> 件 · 庫存 <b>{state['item_count']}</b> 件",
        f"累積 <b>{human_duration(state['ticks'] * TICK_INTERVAL)}</b>",
        f"⏳ 重置 <b>{local_time(end, True)}</b>（還有 {human_duration(end - now)}）",
    ]
    if weekly:
        lines += ["", "<b>最近</b>"]
        for g in reversed(weekly[-3:]):
            lines.append(f"{esc(gift_label(g))} · {local_time(g['time'])}")
    return "\n".join(lines)


TG_COMMANDS = [
    {"command": "stats", "description": "本週掉落進度與重置倒數"},
    {"command": "last", "description": "本週造型一覽（可翻頁）"},
    {"command": "help", "description": "指令說明"},
]

HELP_TEXT = (
    "<b>DST 禮物機器人</b>\n\n"
    "/stats  本週進度、重置倒數、庫存與累積時數\n"
    "/last   本週造型一覽（可翻頁）\n"
    "/help   這份說明\n\n"
    "背景每 6 分鐘送一次心跳，收到禮物會主動通知。"
)


def telegram_setup(token):
    """註冊指令清單與輸入框旁的選單按鈕，Telegram 的 / 按鈕就會列出來。"""
    try:
        _post_json(f"{TG_API}/bot{token}/setMyCommands", {"commands": TG_COMMANDS})
        _post_json(f"{TG_API}/bot{token}/setChatMenuButton",
                   {"menu_button": {"type": "commands"}})
        log("telegram command menu registered")
    except Exception as e:
        log(f"telegram menu setup failed: {e}")


MAIN_KEYBOARD = {
    "keyboard": [[{"text": "📊 本週進度"}, {"text": "🖼 最近掉落"}, {"text": "❓ 說明"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def telegram_send(token, chat, text, html=False, markup=None):
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if html:
        payload["parse_mode"] = "HTML"
    if markup:
        payload["reply_markup"] = markup
    _post_json(f"{TG_API}/bot{token}/sendMessage", payload)


def telegram_answer(token, callback_id, text=None):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        _post_json(f"{TG_API}/bot{token}/answerCallbackQuery", payload, timeout=5)
    except Exception as e:
        log(f"answerCallbackQuery failed: {e}")


def telegram_action(token, chat, action="upload_photo"):
    try:
        _post_json(f"{TG_API}/bot{token}/sendChatAction",
                   {"chat_id": chat, "action": action}, timeout=5)
    except Exception:
        pass


def card_items(state):
    """可翻頁的造型卡：本週有 prefab 的掉落，新的在前。"""
    _, _, weekly = week_counts(state)
    return [g for g in reversed(weekly or state["gifts"]) if g.get("item")]


def pager_markup(index, total):
    row = []
    if index > 0:
        row.append({"text": "⬅️ 上一張", "callback_data": f"last:{index - 1}"})
    row.append({"text": f"{index + 1} / {total}", "callback_data": "noop"})
    if index < total - 1:
        row.append({"text": "下一張 ➡️", "callback_data": f"last:{index + 1}"})
    return {"inline_keyboard": [row]}


def send_card(token, chat, state, index, message_id=None):
    """送出一張造型卡；有 message_id 就原地換圖（Telegram 會標成 edited）。"""
    items = card_items(state)
    if not items:
        return None
    index = max(0, min(index, len(items) - 1))
    gift = items[index]
    path = resolve_icon(gift.get("item"))
    if not path:
        if message_id:
            telegram_answer(token, None, "這件沒有圖")
        return None
    caption = f"<b>{esc(gift_label(gift))}</b>"
    markup = json.dumps(pager_markup(index, len(items)), ensure_ascii=False)
    if message_id:
        media = json.dumps({"type": "photo", "media": "attach://photo",
                            "caption": caption, "parse_mode": "HTML"}, ensure_ascii=False)
        return _post_multipart(f"{TG_API}/bot{token}/editMessageMedia", "photo", path,
                               {"chat_id": chat, "message_id": message_id,
                                "media": media, "reply_markup": markup})
    telegram_action(token, chat)
    return _post_multipart(f"{TG_API}/bot{token}/sendPhoto", "photo", path,
                           {"chat_id": chat, "caption": caption,
                            "parse_mode": "HTML", "reply_markup": markup})


def telegram_loop(state):
    """長輪詢 Telegram，讓 /stats 隨時可查。只有設定了 token 與 chat_id 才會啟動。"""
    tg = CONFIG.get("telegram") or {}
    token = tg.get("token") or os.environ.get("DST_TELEGRAM_TOKEN")
    chat = str(tg.get("chat_id") or os.environ.get("DST_TELEGRAM_CHAT_ID") or "")
    if not token:
        return
    log("telegram listener started" + ("" if chat else ", waiting for the first message to bind"))
    telegram_setup(token)
    offset = 0
    while True:
        try:
            with urllib.request.urlopen(
                    f"{TG_API}/bot{token}/getUpdates?timeout=30&offset={offset}", timeout=40) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            for upd in data.get("result", []):
                offset = max(offset, int(upd.get("update_id", 0)) + 1)

                cq = upd.get("callback_query")      # 造型卡的翻頁按鈕
                if cq:
                    cchat = str(((cq.get("message") or {}).get("chat") or {}).get("id") or "")
                    if chat and cchat == chat:
                        payload = cq.get("data") or ""
                        if payload == "noop":
                            telegram_answer(token, cq.get("id"))
                        elif payload.startswith("last:"):
                            try:
                                num = int(payload.split(":", 1)[1])
                            except ValueError:
                                num = 0
                            telegram_answer(token, cq.get("id"))
                            send_card(token, chat, state, num,
                                      message_id=(cq.get("message") or {}).get("message_id"))
                    continue

                msg = upd.get("message") or {}
                this_chat = str((msg.get("chat") or {}).get("id") or "")
                if not this_chat:
                    continue
                if not chat:                    # 還沒綁定：第一則訊息決定擁有者
                    chat = this_chat
                    CONFIG.setdefault("telegram", {})["chat_id"] = chat
                    save_config()
                    log(f"telegram bound to chat {chat}")
                    telegram_send(token, chat,
                                  "已綁定這個對話，之後只回應這裡。\n輸入 /help 看指令。")
                if this_chat != chat:
                    continue                    # 只理會綁定的那個對話
                text = (msg.get("text") or "").strip().lower()
                if text.startswith(("/start", "/help")) or "說明" in text:
                    telegram_send(token, chat, HELP_TEXT, html=True, markup=MAIN_KEYBOARD)
                elif text.startswith(("/stats", "/status")) or "進度" in text:
                    telegram_send(token, chat, render_progress(state), html=True,
                                  markup=MAIN_KEYBOARD)
                elif text.startswith("/last") or "最近" in text:
                    _, _, weekly = week_counts(state)
                    tail = list(reversed(weekly or state["gifts"]))
                    if not tail:
                        telegram_send(token, chat, "還沒有紀錄", markup=MAIN_KEYBOARD)
                        continue
                    body = "\n".join(
                        [f"<b>本週 {len(tail)} 件</b>"] +
                        [f"{esc(gift_label(g))} · {local_time(g['time'])}" for g in tail])
                    telegram_send(token, chat, body, html=True, markup=MAIN_KEYBOARD)
                    send_card(token, chat, state, 0)
        except Exception as e:
            log(f"telegram poll error: {e}")
            time.sleep(10)


def check_inventory(kl, state, auto_open=True):
    items = kl.inventory()
    if items is None:
        log("inventory query failed")
        return
    count = len(items)
    if state["item_count"] and count > state["item_count"]:
        log(f"inventory grew {state['item_count']} -> {count}")
    state["item_count"] = count
    log(f"holding {count} items")
    if auto_open:
        open_pending(kl, items)


def open_pending(kl, items=None):
    """未開的禮物盒會讓之後的掉落全部卡住（心跳回 E_UNOPENED_ITEMS），所以收到就開。"""
    if items is None:
        items = kl.inventory() or []
    opened = 0
    for i in items:
        if i.get("Context", 1) == 1:        # Context 1 = 一般物品，其他值 = 未開盒
            continue
        item_id, item_type = i["ItemID"], i.get("ItemType", "?")
        res = kl.open_gift(item_id)
        if res.get("Error"):
            log(f"could not open {item_type} ({item_id}): {res.get('ErrorCode')}")
        else:
            log(f"opened {item_type} ({item_id})")
            opened += 1
    return opened


def run_once(kl, state, check_inv=True, auto_open=True):
    result = kl.tick()
    if result.get("ErrorCode") == "E_INVALID_TOKEN":
        kl.login()
        result = kl.tick()
    if result.get("ErrorCode") == "E_UNOPENED_ITEMS" and auto_open:
        log("drops blocked by unopened gifts, clearing them first")
        if open_pending(kl):
            result = kl.tick()
    state["ticks"] += 1
    state["last_tick"] = int(time.time())
    if not record_gifts(state, result, "tick"):
        if result.get("Error"):
            log(f"heartbeat #{state['ticks']} failed: {result.get('ErrorCode') or result}")
        else:
            log(f"heartbeat #{state['ticks']} ok  "
                f"(week {week_count(state)}/8, total {len(state['gifts'])}, "
                f"{human_duration(state['ticks'] * TICK_INTERVAL)} accrued)")
    elif auto_open:
        open_pending(kl)
    if check_inv:
        check_inventory(kl, state, auto_open)
    save_state(state)
    return result


def daemon(kl, state, auto_open=True):
    last_daily = 0
    failures = 0
    log(f"daemon started, one heartbeat every {TICK_INTERVAL} s")
    if (CONFIG.get("telegram") or {}).get("token") or os.environ.get("DST_TELEGRAM_TOKEN"):
        threading.Thread(target=telegram_loop, args=(state,), daemon=True).start()
    while True:
        delay = TICK_INTERVAL
        try:
            # The Klei token expires after ~10-15 minutes, so log in every cycle
            kl.login()
            result = run_once(kl, state, check_inv=(state["ticks"] % INVENTORY_CHECK_EVERY == 0),
                              auto_open=auto_open)
            if result and result.get("Error"):
                failures += 1
            else:
                failures = 0
            if time.time() - last_daily > DAILY_CHECK_INTERVAL:
                if not record_gifts(state, kl.daily_gift(), "daily"):
                    # 每天只在第一次檢查時記一行，免得日誌被「還沒有」灌滿
                    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
                    if state.get("daily_checked") != today:
                        log(f"daily gift: nothing available yet ({today} UTC)")
                        state["daily_checked"] = today
                save_state(state)
                last_daily = time.time()
        except Exception as e:              # 網路或 Steam 出問題：指數退避後重試
            failures += 1
            delay = min(60 * 2 ** (failures - 1), 900)
            log(f"error: {e} (failures={failures}, retry in {delay}s)")
            if failures == 3:
                notify(f"⚠️ DST bot keeps failing\n{e}")
        finally:
            kl.steam.keepalive()

        # 連續失敗到這個程度，光重試沒用，直接把 Steam 連線重建一次
        if failures >= 5:
            log("rebuilding Steam connection")
            try:
                kl.steam = open_steam()
                failures = 0
            except Exception as e:
                log(f"rebuild failed: {e}")

        time.sleep(delay)


def acquire_lock():
    """同一時間只允許一個實例。

    兩個行程各開一個 Steam 工作階段會互相把對方踢掉，看起來就像「跑到一半莫名死掉」。
    鎖在行程結束時由作業系統釋放，所以當掉不會留下死鎖。
    """
    f = open(LOCK_PATH, "a+")
    if os.path.getsize(LOCK_PATH) == 0:
        f.write(".")
        f.flush()
    f.seek(0)
    try:
        if IS_WIN:
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def open_steam(lib=None, timeout=600):
    """A logon task can fire before Steam is ready, so keep retrying instead of dying."""
    deadline = time.time() + timeout
    while True:
        try:
            return Steam(lib)
        except Exception as e:
            if time.time() >= deadline:
                raise
            log(f"waiting for Steam: {e}")
            time.sleep(15)


def selftest():
    import struct
    # Weekly reset is Thursday 21:40 UTC; every key in the game's own drops file lands there
    dt = datetime.datetime.fromtimestamp(1767303600, datetime.timezone.utc)
    assert dt.weekday() == 3 and (dt.hour, dt.minute) == (21, 40), dt
    # Heartbeat interval constant as stored in the client, decoded as float, must be 360 s
    assert struct.unpack("<f", bytes.fromhex("0000b443"))[0] == TICK_INTERVAL
    # 平台分支：Windows 找 .dll，其他平台找 .so
    win_lib = "bin64/steam_api64.dll".replace("/", os.sep)
    nix_lib = "lib64/libsteam_api.so".replace("/", os.sep)
    want = win_lib if IS_WIN else nix_lib
    assert lib_candidates("game")[0] == os.path.join("game", want), lib_candidates("game")
    assert find_lib("no-such-folder") is None
    # 顏色後綴：接得出來的要接，接不出來的不能亂接（需要遊戲才有名字表）
    global GAME_DIR
    game = GAME_DIR or find_game()
    if game:
        GAME_DIR = game
        load_skin_names()
        assert variant_suffix("BODY_BUTTONS_BLUE_SKY") == "Blue Sky"
        assert variant_suffix("LEGS_WORTOX_SURVIVOR") == ""
    print(f"selftest ok (platform={'windows' if IS_WIN else 'posix'}, lib={want})")


def main():
    global GAME_DIR, CONFIG
    CONFIG = load_config()
    ap = argparse.ArgumentParser(
        description="Collect Don't Starve Together drops without launching the game.")
    ap.add_argument("--game", help="path to the DST install folder")
    ap.add_argument("--lib", help="path to steam_api64.dll or libsteam_api.so")
    ap.add_argument("--once", action="store_true", help="run a single cycle and exit")
    ap.add_argument("--no-auto-open", action="store_true",
                    help="leave gift boxes wrapped (they will block further drops)")
    ap.add_argument("--stats", action="store_true", help="print this week's progress and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if args.stats:
        stats(load_state())
        return

    lock = acquire_lock()
    if lock is None:
        log("already running in another process, exiting")
        sys.exit(0)

    GAME_DIR = find_game(args.game)
    if not GAME_DIR:
        sys.exit("Could not find Don't Starve Together. Pass --game \"<path to the game folder>\".")
    log(f"game folder: {GAME_DIR}")
    load_skin_names()
    load_icons()

    steam = open_steam(args.lib)
    kl = Klei(steam)
    state = load_state()
    if args.once:
        kl.login()
        run_once(kl, state, auto_open=not args.no_auto_open)
        log(f"done: {state['ticks']} heartbeats total, {state['item_count']} items")
    else:
        daemon(kl, state, auto_open=not args.no_auto_open)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # pythonw 沒有 stderr，崩潰一定要落進日誌才看得到
        import traceback
        log("FATAL: " + traceback.format_exc().replace("\n", " | "))
        raise
