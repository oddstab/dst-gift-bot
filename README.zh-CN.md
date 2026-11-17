# dontstarve-gift-bot

[繁體中文](README.md) ｜ [English](README.en.md) ｜ [简体中文](README.zh-CN.md)

不用打开游戏，就能领取 **Don't Starve Together** 的挂机礼物和每日礼物。

一个后台运行的 Python 脚本，直接调用游戏自己使用的 Klei 接口、发送同一个 6 分钟心跳，
并记录每一件实际到账的礼物。

> 针对 build `762172` 逆向并实测。所有结论都有反汇编、抓包或 API 实测支撑——
> 详见 [REPORT.md](REPORT.md) 与 [DST_endpoints.md](DST_endpoints.md)。

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
git clone https://github.com/oddstab/dontstarve-gift-bot.git
cd dontstarve-gift-bot
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

要开机自动启动，用任务计划程序指向 `pythonw.exe dst_gift_bot.py`。

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

## 常见问题

**需要安装游戏吗？** 需要。脚本要加载 `steam_api64.dll`，并从安装目录读取 `version.txt`。

**需要游戏在运行吗？** 不需要，这正是重点。

**需要专用服务器吗？** 不需要，而且专用服务器本身产不出掉落——它的可执行文件里
根本没有 `Tick` 这个接口。

**为什么 Steam 会显示我在玩 DST？** Steam 判断「在玩某个游戏」的依据，是有没有进程用
`steam_api64.dll` 为该 appid 建立了 Steam 用户会话，它不看游戏窗口、也不看进程名。
`SteamAPI_Init()` 正是做这件事：通过本地 IPC 连上正在运行的 Steam 客户端，以 `322330`
建立 local user 并注册为运行中，Steam 客户端就会把账号标记为 in-game。
这是取得你自己账号票据的必要代价，关掉脚本就会恢复。匿名初始化
（`SteamAPI_InitAnonymousUser`）虽然不会显示，但也拿不到属于你账号的票据。

**Klei 改了东西怎么办？** `version.txt` 的 build 号是运行时读取的，会自动写进
`User-Agent`，日常更新通常照常可用。若验证流程变了，脚本会记录 `E_INVALID_TOKEN`
并跳过该轮，不会静默失败。

## 注意

- 非官方工具。它用的是游戏自己的接口与 token 流程，但 Klei 并未背书；
  风险等级等同于「把游戏最小化挂着」。
- 每周上限是 8 件计时掉落（外加 1 件每日）。本工具不会提高上限，
  只是不需要开窗口就能达到。
- 只领取不开启。把礼物盒换成皮肤是另一个接口（`/clientitems/DST/SetItemOpened`），尚未实现。

## 文档

- [REPORT.md](REPORT.md) — 完整逆向报告：反汇编地址、360 秒常量、重置排期、
  为什么挂机有效、五种方案的可行性对照。
- [DST_endpoints.md](DST_endpoints.md) — 客户端可执行文件里找到的所有 Klei 接口，
  含请求字段与每一条的把握程度。

## 许可

MIT
