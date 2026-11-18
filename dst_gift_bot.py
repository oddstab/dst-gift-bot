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
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
TICK_INTERVAL = 360                    # matches the float constant at 0x1404f3390 in the client
DAILY_CHECK_INTERVAL = 3600
INVENTORY_CHECK_EVERY = 6              # every 6 heartbeats (36 min)
STATE_PATH = os.path.join(BASE, "state.json")
LOG_PATH = os.path.join(BASE, "dst_gift_bot.log")
LOCK_PATH = os.path.join(BASE, "dst_gift_bot.lock")
CONFIG_PATH = os.path.join(BASE, "config.json")
WEEK_ANCHOR = 1767303600        # 已知的週四 21:40 UTC，用來推算每週重置邊界
CONFIG = {}

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


def notify(title, text=""):
    """可選的推播。沒設定就什麼都不做；失敗只記一行，不影響掛機。"""
    msg = f"{title}\n{text}".strip()
    try:
        url = CONFIG.get("discord_webhook") or os.environ.get("DST_DISCORD_WEBHOOK")
        if url:
            _post_json(url, {"content": msg})
        tg = CONFIG.get("telegram") or {}
        tok = tg.get("token") or os.environ.get("DST_TELEGRAM_TOKEN")
        chat = tg.get("chat_id") or os.environ.get("DST_TELEGRAM_CHAT_ID")
        if tok and chat:
            _post_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                       {"chat_id": chat, "text": msg, "disable_web_page_preview": True})
    except Exception as e:
        log(f"notify failed: {e}")


def _post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=10).read()


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
    log(f"GIFT RECEIVED via {source}: {gift}")
    notify("DST gift received", f"{gift.get('item') or gift.get('key')} ({source})")
    return True


def week_bounds(now=None):
    """目前這一週的起訖時間（週四 21:40 UTC 重置）。"""
    now = int(now or time.time())
    start = now - ((now - WEEK_ANCHOR) % (7 * 86400))
    return start, start + 7 * 86400


def stats(state):
    start, end = week_bounds()
    now = int(time.time())
    weekly = [g for g in state["gifts"] if g["time"] >= start]
    fmt = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"week      {fmt(start)} -> {fmt(end)}")
    print(f"drops     {len(weekly)}/8 this week")
    print(f"reset in  {int((end - now) // 3600)}h {int((end - now) % 3600 // 60)}m")
    print(f"total     {len(state['gifts'])} gifts, {state['ticks']} heartbeats, "
          f"{state['item_count']} items")
    if weekly:
        print("recent:")
        for g in weekly[-5:]:
            print(f"  {fmt(g['time'])}  {g.get('item') or g.get('key')}  [{g['source']}]")


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
            log(f"heartbeat #{state['ticks']} ok")
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
                record_gifts(state, kl.daily_gift(), "daily")
                save_state(state)
                last_daily = time.time()
        except Exception as e:              # 網路或 Steam 出問題：指數退避後重試
            failures += 1
            delay = min(60 * 2 ** (failures - 1), 900)
            log(f"error: {e} (failures={failures}, retry in {delay}s)")
            if failures == 3:
                notify("DST bot keeps failing", str(e))
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
