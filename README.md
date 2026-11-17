# dontstarve-gift-bot

[繁體中文](README.md) ｜ [English](README.en.md) ｜ [简体中文](README.zh-CN.md)

不用開啟遊戲，就能領 **Don't Starve Together** 的掛機禮物與每日禮物。

一支背景執行的 Python 腳本，直接呼叫遊戲自己用的 Klei 端點、送出同一個 6 分鐘心跳，
並記錄每一件實際進帳的禮物。

> 對 build `762172` 逆向與實測。所有結論都有反組譯、封包或 API 實測支撐——
> 詳見 [REPORT.md](REPORT.md) 與 [DST_endpoints.md](DST_endpoints.md)。

---

## 原理

遊戲本身並沒有「玩」出禮物。它在執行時每 360 秒送一次 HTTP 請求：

```
POST https://items.kleientertainment.com/clientitems/DST/Tick
{"Token": "pcl-sing^KU_...^DontStarveTogether^..."}
```

Klei 的物品伺服器就只憑這個心跳累積掉落時間。它從來不知道你在世界裡、在主選單，
還是根本沒開遊戲。本工具送的就是同一個心跳。

```
steam_api64.dll  ->  Steam 驗證票          （不啟動遊戲行程）
      |
      v
POST /login/LoginViaSteam  ->  Klei token   （約 10~15 分鐘失效，每輪更新）
      |
      v
POST /clientitems/DST/Tick        每 360 秒   <- 掉落時間在這裡累積
POST /clientitems/DST/DailyGift   每小時     <- 額外的每日禮物
POST /clientitems/DST/GetAllItems 每 36 分鐘 <- 核對物品是否真的進帳
```

## 需求

- Windows（需要 `steam_api64.dll`）
- Steam 用戶端**要開著並已登入**（腳本不開任何視窗，但 Steam 必須在跑）
- 帳號擁有 Don't Starve Together
- Python 3.8+，只用標準函式庫，不需 pip 安裝任何東西

## 快速開始

```bat
git clone https://github.com/oddstab/dontstarve-gift-bot.git
cd dontstarve-gift-bot
python dst_gift_bot.py --once
```

安裝路徑會自動從 Steam 登錄檔與 `libraryfolders.vdf` 找出來。找不到時自己指定：

```bat
python dst_gift_bot.py --game "D:\Steam\steamapps\common\Don't Starve Together"
```

正式常駐（每 6 分鐘一次心跳，Ctrl+C 停止）：

```bat
python dst_gift_bot.py
```

要開機自動啟動，用工作排程器指向 `pythonw.exe dst_gift_bot.py`。

## 會產生什麼

| 檔案 | 內容 |
|---|---|
| `state.json` | 心跳次數、每件收到的禮物（時間 + item key）、目前物品數 |
| `dst_gift_bot.log` | 每次心跳、登入、掉落各一行 |

真實的一次執行（當時遊戲是關閉的）：

```
23:03:38 logged in as KU_XXXX-XXXX (build 762172)
23:03:40 GIFT RECEIVED via tick: {'time': ..., 'source': 'tick', 'key': 438145790423324876}
23:03:45 inventory grew 2536 -> 2537
```

`Tick` 回傳的 `key`，正好就是庫存裡新增那件物品的 `ItemID`——整條鏈就是這樣對起來的。

## 常見問題

**需要安裝遊戲嗎？** 需要。腳本要載入 `steam_api64.dll`，並從安裝目錄讀 `version.txt`。

**需要遊戲在執行嗎？** 不需要，這正是重點。

**需要專用伺服器嗎？** 不需要，而且專用伺服器本身產不出掉落——它的執行檔根本沒有
`Tick` 這個端點。

**為什麼 Steam 會顯示我在玩 DST？** Steam 判斷「在玩某個遊戲」的依據，是有沒有行程用
`steam_api64.dll` 以該 appid 建立了 Steam 使用者 session，它不看遊戲視窗、也不看行程名稱。
`SteamAPI_Init()` 正是做這件事：透過本機 IPC 連上執行中的 Steam 用戶端，以 `322330`
建立 local user 並註冊成執行中，Steam 用戶端就把帳號標成 in-game。
這是取得你帳號 ticket 的必要代價，關掉腳本就會恢復。匿名登入
（`SteamAPI_InitAnonymousUser`）雖然不會顯示，但也拿不到屬於你帳號的票。

**Klei 改了東西怎麼辦？** `version.txt` 的 build 號是執行時讀的，會自動帶進
`User-Agent`，例行更新通常照跑。若驗證流程變了，腳本會記錄 `E_INVALID_TOKEN`
並中斷該輪，不會默默失敗。

## 注意

- 非官方工具。它用的是遊戲自己的端點與 token 流程，但 Klei 並未背書；
  風險等級等同「把遊戲最小化掛著」。
- 每週上限是 8 件計時掉落（外加 1 件每日）。本工具不會提高上限，
  只是不需要開視窗就能達到。
- 只領不開盒。把禮物盒換成造型是另一支 API（`/clientitems/DST/SetItemOpened`），尚未實作。

## 文件

- [REPORT.md](REPORT.md) — 完整逆向報告：反組譯位址、360 秒常數、重置排程、
  為什麼掛機有效、五種方案的可行性對照。
- [DST_endpoints.md](DST_endpoints.md) — 客戶端執行檔裡找到的所有 Klei 端點，
  含請求欄位與每一條的把握程度。

## 授權

MIT
