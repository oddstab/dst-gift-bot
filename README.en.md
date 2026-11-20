# dst-gift-bot

[繁體中文](README.md) ｜ [English](README.en.md) ｜ [简体中文](README.zh-CN.md)

Collect **Don't Starve Together** timed gifts and the daily gift **without ever launching the game**.

A background Python script that talks to the same Klei endpoints the game itself uses,
sends the same 6-minute heartbeat, and logs every drop it actually receives.

> Reverse-engineered from build `762172`. Every claim is backed by disassembly, captured
> traffic or live API responses — see [REPORT.md](REPORT.md) and [dst-endpoints](https://github.com/oddstab/dst-endpoints).

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
steam_api64.dll  ->  Steam auth session ticket   (no game process)
      |
      v
POST /login/LoginViaSteam  ->  Klei token        (expires in ~10-15 min, refreshed each cycle)
      |
      v
POST /clientitems/DST/Tick        every 360 s    <- this is what earns drops
POST /clientitems/DST/DailyGift   hourly         <- the extra daily gift
POST /clientitems/DST/GetAllItems every 36 min   <- confirms items actually arrived
```

## Requirements

- Windows (uses `steam_api64.dll`)
- Steam client **running and logged in** (the script opens no window, but Steam must be up)
- An account that owns Don't Starve Together
- Python 3.8+, standard library only, no pip installs

## Quick start

```bat
git clone https://github.com/oddstab/dst-gift-bot.git
cd dst-gift-bot
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

### Running on Linux / a VM (optional)

The same script runs on Linux using the `libsteam_api.so` that ships with the native Linux
build of DST. That machine needs a **logged-in Linux Steam client** — Steam *is* the session,
exactly as on Windows.

```bash
python3 dst_gift_bot.py --game ~/.steam/steam/steamapps/common/"Don't Starve Together"
```

The script picks `bin64/steam_api64.dll` or `lib64/libsteam_api.so` based on the platform;
`--lib` overrides it. To keep it running:

```bash
sudo cp dst-gift-bot.service /etc/systemd/system/
sudo systemctl enable --now dst-gift-bot
```

(Adjust `WorkingDirectory` and `User` to your setup.)

> Note: platform detection and error handling were verified on Ubuntu 26.04, but **minting a
> real ticket this way has not been tested**, because it needs `libsteam_api.so` from the Linux
> build of DST. The Windows path is the only one verified end to end.

### Start at logon (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1
```

Registers a task that runs `pythonw.exe` (no console window) 30 seconds after you log in,
restarts it if it dies, and sets no execution time limit. The delay gives Steam time to come
up; the bot also waits for Steam on its own, for up to 10 minutes.

Remove it with:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -Uninstall
```

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

The `key` returned by `Tick` is exactly the `ItemID` that appears in the inventory —
that is the whole loop, proven end to end.

## Notifications and progress (optional)

Copy `config.example.json` to `config.json` and fill in a Discord webhook or a Telegram bot:

```json
{
  "discord_webhook": "https://discord.com/api/webhooks/...",
  "telegram": { "token": "123456:ABC", "chat_id": "12345678" }
}
```

Each gift is pushed with its skin name, and three consecutive failures raise a warning.
Fill in both to get both; leave the file out and nothing is sent. Environment variables
`DST_DISCORD_WEBHOOK`, `DST_TELEGRAM_TOKEN` and `DST_TELEGRAM_CHAT_ID` work too.

With Telegram configured you can also talk to the bot:

| Command | What it does |
|---|---|
| `/stats` | weekly progress bar, reset countdown, inventory and accrued time |
| `/last` | the last five gifts |
| `/help` | the command list |

```
🎁 DST 掉落進度
████████ 8/8 計時掉落

每日禮物 1 件 · 庫存 2556 件
累積 3d 04h
⏳ 重置 Fri 2026-11-27 05:40（還有 6d 01h）

最近
👕 紐扣襯衫 · 2026-11-20 14:19
🪑 精靈頭燈 · 2026-11-20 13:06
```

Gifts arrive as **text only**. The game does not ship skin textures as standalone files:
each skin in `anim_dynamic.zip` contains just a `build.bin` (geometry) pointing at
`atlas-0.texs`, which the engine assembles at runtime. The investigation is written up in
`REPORT.md` under "Local skin art".

Skin names come from the game's own string table (`scripts/skin_strings.lua`, 4145 entries)
with Chinese translations from `scripts/languages/chinese_t.po`. You see the official name
plus its colour suffix (`紐扣襯衫 · Blue Sky`) rather than a prefab code, entirely offline.
A suffix is only added when it is recognisably a colour — a wrong one is worse than none.

> Only one process can poll a bot token at a time (Telegram `getUpdates` limitation), so do
> not point another program at the same token.

See this week's progress (reads the state file only — no Steam, no network):

```bash
python dst_gift_bot.py --stats
```

```
week      2026-11-20 05:40 -> 2026-11-27 05:40  (local)
drops     8/8 timed + 1 daily
reset in  6d 01h  (Fri 2026-11-27 05:40 local)
total     18 gifts, 759 heartbeats (3d 04h), 2556 items
```

## FAQ

### Does the game need to be installed?

Yes. The script loads `steam_api64.dll` and reads
`version.txt` from the install folder.

### Does the game need to be running?

No. That is the entire point.

### Does this need the dedicated server?

No, and a dedicated server cannot earn drops on its
own — its binary has no `Tick` endpoint at all.

### Why does Steam show me as "playing DST"?

Steam decides you are in a game based on whether
some process has created a Steam user session for that app id through `steam_api64.dll` — not
on a game window or a process name. `SteamAPI_Init()` does exactly that: it connects over local
IPC to the running Steam client, creates a local user for `322330` and registers the app as
running, so the client flips your status to in-game. It is the unavoidable cost of getting a
ticket for your own account, and it reverts as soon as the script exits. Anonymous init
(`SteamAPI_InitAnonymousUser`) avoids the status but cannot produce a ticket for your account.

### What if Klei changes something?

The build number is read from `version.txt` at runtime and
goes into the `User-Agent`, so routine updates keep working. If the auth flow changes, the
script logs `E_INVALID_TOKEN` and skips the cycle instead of failing silently.

## Caveats

- Unofficial. It reuses the game's own endpoints and token flow, but Klei has not endorsed it.
  Treat it the same as leaving the game minimised overnight.
- The weekly cap is 8 timed drops (plus 1 daily). This tool does not raise that cap, it just
  reaches it without a game window.
- Gift boxes are **opened automatically**. An unopened box blocks every later drop (the
  heartbeat returns `E_UNOPENED_ITEMS`), so the bot opens them on sight and sweeps leftovers
  during the inventory check. Use `--no-auto-open` to keep them wrapped.

## Documentation

- [REPORT.md](REPORT.md) — full reverse-engineering report: disassembly addresses, the 360 s
  constant, reset schedule, why AFK works, feasibility comparison of five approaches.
- [dst-endpoints](https://github.com/oddstab/dst-endpoints) — a separate repo that probes
  every Klei endpoint found in the client binary against the live servers: which ones still
  work, which are retired, and which auth field each one reads.

## License

MIT
