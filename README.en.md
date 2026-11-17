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
- Gifts are collected, not opened. Turning a gift box into a skin is a separate call
  (`/clientitems/DST/SetItemOpened`) that is not implemented.

## Documentation

- [REPORT.md](REPORT.md) — full reverse-engineering report: disassembly addresses, the 360 s
  constant, reset schedule, why AFK works, feasibility comparison of five approaches.
- [dst-endpoints](https://github.com/oddstab/dst-endpoints) — a separate repo that probes
  every Klei endpoint found in the client binary against the live servers: which ones still
  work, which are retired, and which auth field each one reads.

## License

MIT
