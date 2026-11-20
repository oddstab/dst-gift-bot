# dst-gift-bot

[繁體中文](README.md) ｜ [English](README.en.md) ｜ [简体中文](README.zh-CN.md)

不用打开游戏，就能领取 **Don't Starve Together** 的挂机礼物和每日礼物。

一个后台运行的 Python 脚本，直接调用游戏自己使用的 Klei 接口、发送同一个 6 分钟心跳，
并记录每一件实际到账的礼物。

> 针对 build `762172` 逆向并实测。所有结论都有反汇编、抓包或 API 实测支撑——
> 详见 [REPORT.md](REPORT.md) 与 [dst-endpoints](https://github.com/oddstab/dst-endpoints)。

---

## 原理

游戏本身并没有「玩」出礼物。它在运行时每 360 秒发送一次 HTTP 请求：

```
POST https://items.kleientertainment.com/clientitems/DST/Tick
{"Token": "pcl-sing^KU_...^DontStarveTogether^..."}
```

Klei 的物品服务器只凭这个心跳累积掉落时间。它从来不知道你是在世界里、在主菜单，
还是根本没开游戏。本工具发送的就是同一个心跳。

```
steam_api64.dll  ->  Steam 验证票据        （不启动游戏进程）
      |
      v
POST /login/LoginViaSteam  ->  Klei token   （约 10~15 分钟失效，每轮更新）
      |
      v
POST /clientitems/DST/Tick        每 360 秒   <- 掉落时间在这里累积
POST /clientitems/DST/DailyGift   每小时     <- 额外的每日礼物
POST /clientitems/DST/GetAllItems 每 36 分钟 <- 核对物品是否真的到账
```

## 需求

- Windows（需要 `steam_api64.dll`）
- Steam 客户端**需要开着并已登录**（脚本不打开任何窗口，但 Steam 必须在运行）
- 账号拥有 Don't Starve Together
- Python 3.8+，只用标准库，不需要 pip 安装任何东西

## 快速开始

```bat
git clone https://github.com/oddstab/dst-gift-bot.git
cd dst-gift-bot
python dst_gift_bot.py --once
```

安装路径会自动从 Steam 注册表和 `libraryfolders.vdf` 找出来。找不到时自行指定：

```bat
python dst_gift_bot.py --game "D:\Steam\steamapps\common\Don't Starve Together"
```

正式常驻（每 6 分钟一次心跳，Ctrl+C 停止）：

```bat
python dst_gift_bot.py
```

### 跑在 Linux / VM 上（可选）

同一个脚本也能在 Linux 跑，靠的是 Linux 版 DST 附带的 `libsteam_api.so`。该机器上要有一个
**已登录的 Linux 版 Steam 客户端**（Steam 就是会话本身，这点跟 Windows 一样）。

```bash
python3 dst_gift_bot.py --game ~/.steam/steam/steamapps/common/"Don't Starve Together"
```

脚本会按平台自动挑选 `bin64/steam_api64.dll` 或 `lib64/libsteam_api.so`，也可以用 `--lib` 指定。
要常驻：

```bash
sudo cp dst-gift-bot.service /etc/systemd/system/
sudo systemctl enable --now dst-gift-bot
```

（`WorkingDirectory` 与 `User` 记得按你的环境改。）

> 注意：这条路径已在 Ubuntu 26.04 验证过平台判断与错误处理，但**实际取票尚未实测**，
> 因为需要 Linux 版 DST 的 `libsteam_api.so`。Windows 路径是唯一经过完整验证的。

### 开机自动启动（Windows 任务计划程序）

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1
```

会用 `pythonw.exe`（不弹窗口）在登录后 30 秒启动，进程挂掉自动重启，且不设运行时间上限。
延迟是留给 Steam 连接的时间；机器人本身也会等 Steam 就绪，最多等 10 分钟。

要移除：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -Uninstall
```

## 会生成什么

| 文件 | 内容 |
|---|---|
| `state.json` | 心跳次数、每件收到的礼物（时间 + item key）、当前物品数 |
| `dst_gift_bot.log` | 每次心跳、登录、掉落各一行 |

真实的一次运行（当时游戏是关闭的）：

```
23:03:38 logged in as KU_XXXX-XXXX (build 762172)
23:03:40 GIFT RECEIVED via tick: {'time': ..., 'source': 'tick', 'key': 438145790423324876}
23:03:45 inventory grew 2536 -> 2537
```

`Tick` 返回的 `key`，正好就是库存里新增那件物品的 `ItemID`——整条链路就是这样对上的。

## 通知与进度（可选）

复制 `config.example.json` 成 `config.json`，填入 Discord webhook 或 Telegram bot：

```json
{
  "discord_webhook": "https://discord.com/api/webhooks/...",
  "telegram": { "token": "123456:ABC", "chat_id": "12345678" }
}
```

收到礼物时推送皮肤名，连续失败 3 次也会通知。两个都填就两边都发，都不填就完全不发。
也可以用环境变量 `DST_DISCORD_WEBHOOK`、`DST_TELEGRAM_TOKEN` + `DST_TELEGRAM_CHAT_ID`。

设置好 Telegram 之后，可以直接对机器人下指令：

| 指令 | 作用 |
|---|---|
| `/stats` | 本周进度条、重置倒计时、库存与累积时长 |
| `/last` | 本周造型一览（可翻页的造型卡） |
| `/help` | 指令说明 |

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

收到礼物时会**附上皮肤图**，而且是从你本机安装的游戏现场合成的：
`data/anim/dynamic/<prefab>.dyn` 解开后是一张 `KTEX`（BC3）图集，裁出最大的一块就是造型。
全程不连接任何外部网站。格式细节见 `REPORT.md` 的 §A.7。

皮肤名称取自游戏自己的字符串表（`scripts/skin_strings.lua` 的 4145 笔），
繁中翻译来自 `scripts/languages/chinese_t.po`。显示的是官方名称加颜色后缀
（例：`紐扣襯衫 · Blue Sky`），不是 `BODY_BUTTONS_BLUE_SKY` 这种代码，
而且完全离线、不需要连接任何第三方网站。后缀只在能确定是颜色时才加——接错比不接更糟。

> 一个 bot token 同时只能有一个程序在收指令（Telegram `getUpdates` 的限制），
> 所以不要让别的程序轮询同一个 token。

查看本周进度（纯本地读文件，不需要 Steam、不联网）：

```bash
python dst_gift_bot.py --stats
```

```
week      2026-11-20 05:40 -> 2026-11-27 05:40  (local)
drops     8/8 timed + 1 daily
reset in  6d 01h  (Fri 2026-11-27 05:40 local)
total     18 gifts, 759 heartbeats (3d 04h), 2556 items
```

## 常见问题

### 需要安装游戏吗？

需要。脚本要加载 `steam_api64.dll`，并从安装目录读取 `version.txt`。

### 需要游戏在运行吗？

不需要，这正是重点。

### 需要专用服务器吗？

不需要，而且专用服务器本身产不出掉落——它的可执行文件里
根本没有 `Tick` 这个接口。

### 为什么 Steam 会显示我在玩 DST？

Steam 判断「在玩某个游戏」的依据，是有没有进程用
`steam_api64.dll` 为该 appid 建立了 Steam 用户会话，它不看游戏窗口、也不看进程名。
`SteamAPI_Init()` 正是做这件事：通过本地 IPC 连上正在运行的 Steam 客户端，以 `322330`
建立 local user 并注册为运行中，Steam 客户端就会把账号标记为 in-game。
这是取得你自己账号票据的必要代价，关掉脚本就会恢复。匿名初始化
（`SteamAPI_InitAnonymousUser`）虽然不会显示，但也拿不到属于你账号的票据。

### Klei 改了东西怎么办？

`version.txt` 的 build 号是运行时读取的，会自动写进
`User-Agent`，日常更新通常照常可用。若验证流程变了，脚本会记录 `E_INVALID_TOKEN`
并跳过该轮，不会静默失败。

## 注意

- 非官方工具。它用的是游戏自己的接口与 token 流程，但 Klei 并未背书；
  风险等级等同于「把游戏最小化挂着」。
- 每周上限是 8 件计时掉落（外加 1 件每日）。本工具不会提高上限，
  只是不需要开窗口就能达到。
- 礼物盒会**自动开启**。未开的盒子会让之后的掉落全部卡住（心跳返回 `E_UNOPENED_ITEMS`），
  所以机器人一收到就开，查库存时也会顺手清掉残留的盒子。要保留不开用 `--no-auto-open`。

## 文档

- [REPORT.md](REPORT.md) — 完整逆向报告：反汇编地址、360 秒常量、重置排期、
  为什么挂机有效、五种方案的可行性对照。
- [dst-endpoints](https://github.com/oddstab/dst-endpoints) — 另一个仓库，把客户端
  可执行文件里所有 Klei 接口逐一实测，记录哪些还活着、哪些已下线、各自使用哪个认证字段。

## 许可

MIT
