# dontstarve-gift-bot

Collect **Don't Starve Together** timed gifts and the daily gift **without ever launching the game**.

A background Python script that talks to the same Klei endpoints the game itself uses,
sends the same 6-minute heartbeat, and logs every drop it receives.

> Reverse-engineered from build `762172`. Everything below is backed by disassembly,
> captured traffic and live API responses — see [REPORT.md](REPORT.md) and [DST_endpoints.md](DST_endpoints.md).

---

## How it works

The game does not "play" to earn gifts. While it is running it sends one HTTP request every
360 seconds:

```
POST https://items.kleientertainment.com/clientitems/DST/Tick
{"Token": "pcl-sing^KU_...^DontStarveTogether^..."}
```

Klei's item server credits drop time purely from that heartbeat. It never learns whether you
are in a world, in the main menu, or not running at all. This tool sends the same heartbeat.

```
steam_api64.dll  ->  Steam auth session ticket   (no game process, no game launch)
      |
      v
POST /login/LoginViaSteam  ->  Klei token        (lives ~10-15 min, refreshed every cycle)
      |
      v
POST /clientitems/DST/Tick        every 360 s    <- this is what earns drops
POST /clientitems/DST/DailyGift   hourly         <- the extra daily gift
POST /clientitems/DST/GetAllItems every 36 min   <- confirms items actually arrived
```

## Requirements

- Windows (uses `steam_api64.dll`)
- Steam client **running and logged in** (the script never opens a window, but Steam must be up)
- An account that owns Don't Starve Together
- Python 3.8+ (standard library only, no pip installs)

## Quick start

```bat
git clone https://github.com/oddstab/dontstarve-gift-bot.git
cd dontstarve-gift-bot
python dst_gift_bot.py --once
```

The install folder is auto-detected from the Steam registry and `libraryfolders.vdf`.
Override it if needed:

```bat
python dst_gift_bot.py --game "D:\Steam\steamapps\common\Don't Starve Together"
```

Run it for real (one heartbeat every 6 minutes, Ctrl+C to stop):

```bat
python dst_gift_bot.py
```

To keep it around after a reboot, point Task Scheduler at `pythonw.exe dst_gift_bot.py`.

## What you get

| File | Meaning |
|---|---|
| `state.json` | heartbeat count, every gift received (timestamp + item key), item count |
| `dst_gift_bot.log` | one line per heartbeat / login / drop |

A real run, with the game closed:

```
23:03:38 logged in as KU_XXXX-XXXX (build 762172)
23:03:40 GIFT RECEIVED via tick: {'time': ..., 'source': 'tick', 'key': 438145790423324876}
23:03:45 inventory grew 2536 -> 2537
```

The `key` returned by `Tick` is the `ItemID` that shows up in the inventory — that is the
whole loop proven end to end.

## FAQ

**Does the game need to be installed?** Yes, the script loads `steam_api64.dll` and reads
`version.txt` from the install.

**Does the game need to be running?** No. That is the entire point.

**Does this need the dedicated server?** No, and a dedicated server cannot earn drops on its
own — its binary has no `Tick` endpoint at all.

**Will Steam show me as "playing DST"?** Yes. `SteamAPI_Init()` creates a real session for
app id 322330, so friends will see the game as running. There is no way around this on this route.

**What if Klei changes something?** The build number in `version.txt` is read at runtime and
used in the `User-Agent`, so routine updates keep working. If the auth flow changes, the script
logs `E_INVALID_TOKEN` and exits the cycle instead of silently failing.

## Caveats

- Unofficial. It reuses the game's own endpoints and token flow, but Klei has not endorsed it.
  Treat it the same as leaving the game minimised overnight.
- The weekly cap is 8 timed drops (plus 1 daily). This tool does not raise that cap, it just
  reaches it without a game window.
- Gifts are collected, not opened. Opening a gift box into a skin is a separate call
  (`/clientitems/DST/SetItemOpened`) that is not implemented.

## Documentation

- [REPORT.md](REPORT.md) — full reverse-engineering report: disassembly addresses, the 360 s
  constant, reset schedule, why AFK works, feasibility comparison of five approaches.
- [DST_endpoints.md](DST_endpoints.md) — every Klei endpoint found in the client binary,
  with request body fields and how confident each entry is.

## License

MIT

---

# 中文說明

不用開遊戲就能領 Don't Starve Together 的掛機禮物與每日禮物。

## 原理

遊戲本身每 360 秒送一次心跳 `POST /clientitems/DST/Tick`，Klei 的物品伺服器就靠這個心跳
累積掉落時間。它不知道你在世界裡、在主選單、還是根本沒開遊戲。本工具送的就是同一個心跳。

## 需求

- Windows
- Steam 用戶端**要開著並已登入**（工具本身不開任何視窗）
- 帳號擁有 DST
- Python 3.8+，只用標準函式庫

## 用法

```bat
git clone https://github.com/oddstab/dontstarve-gift-bot.git
cd dontstarve-gift-bot
python dst_gift_bot.py --once     :: 先跑一輪確認可以通
python dst_gift_bot.py            :: 常駐，每 6 分鐘一次心跳
```

遊戲路徑會自動從 Steam 登錄檔與 `libraryfolders.vdf` 找出來；找不到就自己指定：

```bat
python dst_gift_bot.py --game "D:\Steam\steamapps\common\Don't Starve Together"
```

## 實測結果

工具上線 32 分鐘後收到第一件掉落，`Tick` 回傳的 `key` 與庫存新增物品的 `ItemID` 完全相同，
當時遊戲是關閉的。詳見 [REPORT.md](REPORT.md)。

## 注意

- 非官方工具，風險等級等同「把遊戲最小化掛著」。
- Steam 會顯示你正在遊玩 DST，好友看得到，這點無法避免。
- 只領不開盒；把禮物盒換成造型是另一支 API，尚未實作。
- 每週上限 8 件計時掉落 + 1 件每日，本工具不會提高上限，只是不需要開視窗就能達到。
