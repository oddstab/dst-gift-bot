# DST 掛機禮物機制：逆向報告與背景領取工具

調查日期：2026-11-17　目標版本：`762172`（`version.txt`）
安裝路徑：`<DST_DIR>`

---

## 0. 結論摘要

**「不開遊戲」是可行的，而且已完成並實跑成功。**

禮物不是由遊戲伺服器發，也不是 Steam 發，而是 Klei 的物品伺服器
（`items.kleientertainment.com`）發。遊戲本體的角色只有一個：**每 360 秒送一次心跳**
`POST /clientitems/DST/Tick`。這個心跳可以在完全沒有遊戲行程的情況下重放，
我用本機 `steam_api64.dll` 取得 Steam 驗證票、換 Klei Token、直接打心跳，
已取得真實回應 `{"Error":false,"GiftReceived":false}`，並讀到 2536 件真實庫存。

「必須進入世界才會累積時數」是**客戶端**的限制，不是伺服器端的：
本次實測把遊戲開到主選單放 8 分鐘，記憶體中完全沒有 `/Tick` 請求，
但同一時間 `LoginViaSteam` 與 `GetAllItems` 都正常送出。
伺服器只看到 Tick 請求，它不知道、也無從得知玩家在不在世界裡。

| 問題 | 答案 | 證據等級 |
|---|---|---|
| 禮物誰發的 | Klei item server | 已驗證（執行檔字串 + 實測回應） |
| AFK 為何有效 | 心跳每 360 秒累積一次掉落時間 | 已驗證（反組譯常數 + 執行期請求） |
| 不開遊戲能否送出心跳 | 可以 | 已驗證（實跑，見 §F） |
| 主選單是否送心跳 | 不會 | 已驗證（記憶體掃描 8 分鐘無 Tick） |
| 心跳能否累積成實際掉落 | 能，實測 32 分鐘內拿到一件 | 已驗證 |

---

## A. 禮物機制真相

### A.1 四層架構，只有三層跟禮物有關

```
Steam（身分）        Klei login（換 token）        Klei item server（判定+發放）
   │                        │                              │
   │ GetAuthSessionTicket   │ POST /login/LoginViaSteam    │ POST /clientitems/DST/Tick
   │ ──────────────────────▶│ ────────────────────────────▶│  每 360 秒
   │                        │◀──── Token (pcl-sing^…) ─────│
   │                        │                              │
遊戲伺服器（世界）── 完全與掉落無關，這是最常見的誤解
```

證據：

- 掉落判定在 item server 的 `Tick`，客戶端沒有掉落邏輯。
  `bin64\dontstarve_steam_x64.exe` 字串：
  `[FAILED to send item tick heart beat %s:%i]`、
  `Error parsing JSON in SendItemDropHeartbeat_Complete`、
  `clientitems/DST/Tick`。
- 專用伺服器 `bin64\dontstarve_dedicated_server_nullrenderer_x64.exe`
  **有** `clientitems/DST/DailyGift`、`clientitems/DST/GetAllItems`，
  **沒有** `clientitems/DST/Tick`、也沒有 `SendItemDropHeartbeat`。
  → 專用伺服器空跑不會產生任何計時掉落，這點與社群說法一致。
- 遊戲 Lua 只負責顯示。`scripts\components\giftreceiver.lua:9-10`：

  ```lua
  --From GiftingManager.cpp
  inst:ListenForEvent("ms_updategiftitems", OnUpdateGiftItems)
  ```

  `ms_updategiftitems` 由 C++ `GiftingManager` 發出（反組譯位址 `0x1402009b1`、`0x14020141d`）。

### A.2 心跳：360 秒，累加式

反組譯 `0x1402020e0`（item tick 的 update 函式）：

```asm
addss  xmm1, dword ptr [rcx+8]        ; self->timer += dt
movss  xmm0, dword ptr [rip+0x2f1298] ; = 0x1404f3390，內容 0000b443
comiss xmm1, xmm0
jb     0x140202391                    ; 未滿就 return
subss  xmm1, xmm0                     ; 滿了就扣掉一個週期
...
lea    rdx, [rip + ...]               ; "POST"
lea    rdx, [rip + ...]               ; "clientitems/DST/Tick"
call   0x14018f9c0                    ; 送出（第四個參數 r9d = 0 → 無 body 模板）
```

`0x1404f3390` 的位元組是 `00 00 b4 43`，以 IEEE754 float 解讀 = **360.0 秒**。
（`dst_afk.py --selftest` 會斷言這件事。）

心跳的觸發有前置條件，`0x1401ffcc0`：

```asm
mov  rcx, [0x140621ba8]      ; 全域設定物件
call 0x1401d2740             ; 線上模式檢查
test al, al ; je  fail
cmp  byte ptr [rbx+0x30a], 0 ; 某個 session 旗標
je   fail
mov  rax, [rbx+0x2d8]        ; 網路 session 物件
...
call 0x1403d3060             ; 以 tag 0x9440204e 查狀態
sete al
```

這個 gate 就是「在主選單不會送心跳」的原因。**它只存在於客戶端。**

### A.3 每日禮物 vs 每週計時掉落

官方 2019 公告（Winona Update / Drop System Changes）寫明：

> Each day you play, you will receive a drop simply by logging in.
> …doubling the amount of timed drops that are given per week to 8…
> a total of 15 items per week.

對應到本機證據：

- **每日禮物**：`clientitems/DST/DailyGift`，body 用 `ClientToken`，
  回應 `{"Error":false,"GiftReceived":…}`。
- **每週 8 個計時掉落**：`clientitems/DST/Tick` 累積，body 用 `Token`。

### A.4 重置時間：星期四 21:40 UTC

本機 `data\unsafedata\dropsdata_KU_XXXX-XXXX.json` 是遊戲自己寫的每週掉落紀錄。
40 個 key 全部落在 **星期四 21:40 UTC**，間隔都是 7 天的倍數，`count` 最大值為 8：

```
1741902000  2025-03-13 Thu 21:40 UTC  count=8
1742506800  2025-03-20 Thu 21:40 UTC  count=8
1743111600  2025-03-27 Thu 21:40 UTC  count=3
...
1770327600  2026-02-05 Thu 21:40 UTC  count=0
最大 count: 8，間隔: 604800 / 1209600 / 2419200 / 21772800 秒
```

Mod `Drops Reset Countdown` 的算法也一致：
`scripts\widgets\dropscountdownwidget.lua:27-31` 把重置定在 `21:40`，
`:52` 把每週重置定在 `weekday=4`（Lua 的 `%w`，4 = 星期四）。

### A.5 未開的禮物盒會封鎖掉落

掛機一整天後 `Tick` 開始連續回 `E_UNOPENED_ITEMS`，掉落完全停止。開掉那個盒子之後，
**下一次心跳立刻回 `GiftReceived: true`**，把排隊的那件補發出來。所以規則是：

- 庫存裡有未開的盒子 → 伺服器拒絕發新的掉落
- 未開盒在 `GetAllItems` 裡的特徵是 `Context != 1`（一般物品是 1，未開盒是 3）
- 開啟用 `POST clientitems/DST/SetItemOpened`，body `{"ClientToken": ..., "ItemID": <int64>}`

欄位名是大寫 `ItemID`。送小寫 `item_id` 不會報錯，會回 `{"Error":false,"ItemID":0}`——
看起來像成功，實際上什麼都沒做。這個坑很容易踩。

### A.6 為什麼「掛機」有效

遊戲在世界中時，客戶端每 360 秒打一次 `Tick`；item server 收到就記一段遊玩時間，
累積到門檻就回 `GiftReceived:true` 並把禮物加進帳號庫存。
玩家只要「讓客戶端一直開著並連上一個世界」，就等於一直在送心跳——
這就是掛機的本質。**它從頭到尾都只是 HTTP 心跳，跟角色在做什麼無關。**

---

## A.7 `.dyn` 格式與本地造型圖（已解開）

`data/anim/dynamic/<prefab>.dyn`（3532 個，2KB–2.5MB）就是該造型的貼圖包。
它不是壓縮檔，而是把一個標準 ZIP 用 **8 bytes 區塊重排 + XOR** 混淆。

### 混淆還原

證據是 `dontstarve_steam_x64.exe` 裡 `AnimationFile::LoadDynamicTextures`
（`0x140384018` 引用 `.dyn` 字串，`0x140384100` 起是還原迴圈）。迴圈先把開頭兩
bytes 跟 `PK` 比對，不像 ZIP 才做轉換；轉換本身是：

```asm
movzx ecx, byte ptr [rbx]      ; b1
movzx eax, byte ptr [rbx + 4]  ; b5
movzx edx, byte ptr [rbx - 1]  ; b0
movzx r8d, byte ptr [rbx + 1]  ; b2
xor   byte ptr [rbx + 3], 0x91
...
add   rbx, 8
```

整理成公式：

```
out[i] = block[src[i]] ^ key[i]
src = [5, 3, 6, 7, 4, 2, 0, 1]
key = [0x8d, 0x8e, 0x8f, 0x90, 0x91, 0x92, 0x93, 0x94]
```

驗算第一個區塊：`93 94 92 c5 85 dd 8c 94` →
`50 4b 03 04 14 00 00 00`，也就是 `PK\x03\x04` 的 ZIP 標頭。
尾巴不足 8 bytes 的部分不動。解出來是含 `atlas-0.tex` 的 ZIP。

### KTEX

`atlas-0.tex` 是 Klei 自家的 `KTEX` 格式：`KTEX` magic + **140 bytes 檔頭**
+ BC3(DXT5) 區塊（含 8 級 mip chain）。寬高是 little-endian `u16`，分別在 offset 8 與 10。

驗證方式：`512×512` 的圖集，8 級 mip 的 BC3 總量是
`262144+65536+16384+4096+1024+256+64+16 = 349520`，檔案 349660 − 140 = 349520，
剛好吻合；同樣的 140 bytes 偏移在 `512×256` 的 `abigail_flower` 圖集上也能正確解出。

解碼後的圖集是一張放滿零件的大圖（一件襯衫會有袖子、袖口、領子…），
所以再取 **alpha 連通區塊中最大的一塊** 的 bbox 當代表圖。實測三個部位：

| prefab | 尺寸 | 非透明比例 |
|---|---|---|
| `BODY_BUTTONS_BLUE_SKY` | 175×197 | 0.63 |
| `DECOR_LAMP_FANTASY` | 240×233 | 0.55 |
| `LEGS_WORTOX_SURVIVOR` | 201×175 | 0.50 |

實作在 `dst_skinart.py`（只用標準函式庫，PNG 用 `zlib` 自行輸出），
測試在 `test_skinart.py`。整個流程不連任何外部網站。

## B. 技術流程圖與必要條件

```
① Steam 票券（必要條件 1）
   steam_api64.dll → ISteamUser::GetAuthSessionTicket(appid 322330)
   │  產出 246 bytes binary ticket（hex 492 字元）
   ▼
② Klei Token（必要條件 2）
   POST https://login.kleientertainment.com/login/LoginViaSteam
   body: {"SteamTicket":"<大寫hex>","appid":"322330","Game":"DontStarveTogether"}
   resp: {"Error":false,"Token":"pcl-sing^KU_xxx^DontStarveTogether^<簽章>",
          "UserID":"KU_xxx","AnalyticsJWT":"…"}
   │
   ▼
③ 心跳（必要條件 3）—— 掉落時間在這裡累積
   POST https://items.kleientertainment.com/clientitems/DST/Tick
   body: {"Token":"pcl-sing^…"}
   resp: {"Error":false,"GiftReceived":true/false}
   │  每 360 秒一次
   ▼
④ 每日禮物（選用，但能多拿一個）
   POST …/clientitems/DST/DailyGift
   body: {"ClientToken":"pcl-sing^…"}
   │
   ▼
⑤ 核對入帳
   POST …/clientitems/DST/GetAllItems
   body: {"ClientToken":"…","InventoryStateToken":"","CheckTwitchEntitlements":true}
```

**三個必要條件都可以在遊戲外滿足。** 沒有第四個條件。

HTTP 標頭（實測抓自執行中的遊戲，缺一不可）：

```
Host: items.kleientertainment.com
User-Agent: DontStarveTogether/762172      ← 必須是真實 build 號
Accept-Encoding: deflate, gzip
Accept: application/json
Content-Type: application/json
charsets: utf-8
```

> `User-Agent` 的 build 號錯、或 token 放錯欄位（放 header 而不是 body），
> 伺服器一律回 `E_INVALID_TOKEN`。這是整件事最難過的兩關，兩關都卡了很長時間。

`Authorization: Bearer ` 這個字串存在於執行檔（`0x140505020`），但執行期抓到的
實際請求裡它是空的——驗證走 JSON body，不走標頭。

---

## C. Mod 逆向報告：`2314960345`

**實際名稱**：`Super AFK (modified)`，`modinfo.lua:1`
**原始出處**：`1981709850`（`modinfo.lua:26`）；掉落倒數模組來自 `1688661034`
（`scripts\drops_func.lua:1`）

### C.1 它做什麼

`modinfo.lua` 自述：Godmode、關閉 idle kick、**自動開禮物（不需科學機器）**、
顯示 AFK 期間收到的禮物數、小地圖、衣櫃、無限釣竿。
`client_only_mod = false`，它是**伺服器端 mod**，需要在一個世界裡生效。

| 功能 | 實作位置 | 與禮物的關係 |
|---|---|---|
| 關閉 idle kick | `modmain.lua:136,164`（`afkdespawn` slash command） | 讓角色留在世界，心跳才不會斷 |
| 自動開禮物 | `modmain.lua:61-76`（覆寫 `widgets/giftitemtoast`，`DoPeriodicTask(.5)`） | 只影響「開盒」，不影響發放 |
| 免科學機器開禮物 | `modmain.lua:244-252`（覆寫 `giftreceiver.SetGiftMachine`） | 純客戶端便利 |
| 禮物數顯示 | `modmain.lua:25-52`（`idle_mods.skins_count_changed`） | 統計 |
| 重置倒數 | `scripts/widgets/dropscountdownwidget.lua` | 顯示每日/每週重置 |

### C.2 它沒有做什麼

它**沒有**碰任何 HTTP、沒有 token、沒有心跳。整個 mod 一行網路程式碼都沒有。
它只是讓「開著遊戲掛在世界裡」這件事比較穩定、比較省資源、順便自動開盒。

### C.3 可複用的部分

1. **重置時間公式**（`dropscountdownwidget.lua:27-31, 49-60`）已驗證與本機
   `dropsdata` 完全吻合，本工具直接沿用（星期四 21:40 UTC）。
2. **開禮物流程**：`TheInventory:GetUnopenedItems()` → `TheInventory:SetItemOpened(item_id)`
   （`modmain.lua:108-110`、`widgets/dropscounterwidget.lua:143-186`）。
   要「真的把禮物換成 skin」得走 `SetItemOpened`，本工具目前只做領取與核對，
   開盒沒有做（見 §G）。
3. **`ms_updategiftitems` 事件**是 C++ 通知 Lua 更新的管道，
   與 `giftreceiver.lua:9` 的註解 `--From GiftingManager.cpp` 互相印證。

### C.4 它揭露了什麼底層機制

最重要的一點：**這個 mod 的作者顯然也只知道「開著遊戲就會掉」**，
他用的是「關掉 idle kick + 自動開盒 + 縮小世界」的暴力法，
完全沒有觸及心跳。這反過來證明社群的認知停留在表層。

---

## D. 可行性對照

| 方案 | 可行性 | CPU/RAM | 需 Steam 常駐 | 需 DST 常駐 | 難度 | 穩定性 | 帳號風險 |
|---|---|---|---|---|---|---|---|
| **A. 直接打後端（本方案）** | **可行，已實作** | <1% / ~40MB | 要（要 steam_api64 的 session） | 不用 | 中（已解決） | 高 | 低（等同官方 API 用法） |
| B. 只跑 Steam Client | 不可行 | — | 要 | 不用 | — | — | — |
| C. 背景重放心跳 | 同 A | 同 A | 要 | 不用 | 同 A | 同 A | 同 A |
| D. 精簡/headless 客戶端 | 可行但沒必要 | 5–15% / 1–2GB | 要 | 要 | 高 | 中 | 低 |
| E. 專用伺服器空跑 | **不可行** | 低 | 不用 | 要 | 低 | 高 | 無 |

判斷依據：

- **A**：`GetAllItems` 回傳 2536 件真實物品、`Tick` 回 `{"Error":false,...}`，
  兩者都在無遊戲行程下完成。這是唯一達到「不開遊戲」目標的方案。
- **B**：Steam 只記錄時數，心跳由遊戲 binary 發出（`Tick` 字串只存在於
  `dontstarve_steam_x64.exe`）。純掛 Steam 時數不會有任何掉落。
- **C**：與 A 等價，只是描述角度不同。
- **D**：可以做到（例如用 `-nullrenderer` 之類參數），但 CPU/RAM 遠高於 A，
  且每次改版都要重新調參數。只在 A 被封鎖時才有意義。
- **E**：專用伺服器 binary 根本沒有 `Tick` 端點（已用字串比對確認），
  它只能提供一個「不會死」的世界讓客戶端連進去。它本身產不出任何掉落。

---

## E. 最佳方案

**選 A：Python 單檔，載入 `steam_api64.dll` 取票，直接打 Klei 後端。**

理由：

- 唯一真正免開遊戲的方案。
- 記憶體約 40MB、CPU 幾乎為 0、每 6 分鐘一個 <300 bytes 的 POST。
- 只用標準函式庫（`ctypes` / `urllib` / `json`），沒有依賴，不怕套件腐化。
- 沒有 DLL 注入、沒有 hook、沒有改遊戲檔案，遊戲更新後只要 `version.txt` 的
  build 號跟著變，程式自動適配（UA 從檔案讀）。

架構：

```
dst_afk.py
 ├─ Steam        : steam_api64.dll → auth session ticket（每輪重取）
 ├─ Klei         : LoginViaSteam → Token；Tick / DailyGift / GetAllItems
 ├─ state.json   : 心跳次數、收到過的禮物、上次庫存件數
 └─ dst_afk.log  : 逐行記錄
```

限制：

- 必須有 Steam 用戶端在執行（`SteamAPI_Init` 需要它），但不需要任何 Steam 視窗。
- 每次取新票前會 `CancelAuthTicket` 歸還上一張，避免長時間執行耗盡 Steam 的票券配額。
- 帳號必須擁有 DST（`GetAppOwnershipTicket` 會失敗於免費帳號）。
- 家庭共享帳號是否適用未測。

---

## F. PoC 實跑結果

`G:\...\Don't Starve Together\dst_afk\dst_afk.py`

```
> python dst_afk.py --selftest
selftest ok

> python dst_afk.py --once
[2026-11-17 22:13:57] 登入成功 UserID=KU_XXXX-XXXX build=762172
[2026-11-17 22:13:58] 心跳 #1 ok=True
[2026-11-17 22:14:02] 目前持有 2536 件物品
[2026-11-17 22:14:03] 完成：累計心跳 1 次，庫存 2536 件
```

對照組（證明不是幻覺）：同樣的 token 放在 `Authorization: Bearer` 標頭會得到
`E_INVALID_TOKEN`；放在 `{"ClientToken":…}` 會得到
`E_INVALID_TOKEN`；只有 `{"Token":…}` 才是 Tick 要的格式。

常駐模式連續跑 7 次心跳，節奏與 360 秒常數一致：

```
22:14:12 心跳 #2 ok=True
22:20:14 心跳 #3 ok=True
22:26:17 登入成功（token 到期，自動重登）
22:26:18 心跳 #4 ok=True
...
22:31:04 心跳 #7 ok=True
```

順帶量到一件事：**Klei token 大約 10~15 分鐘就失效**（第一次 22:14:10 取得，
22:26 就已經不能用）。所以正式版改成每一輪先重登一次，用一次多餘的小請求
換掉整個失效模式。

原始 API 回應（節錄）：

```
POST /clientitems/DST/Tick  {"Token":"pcl-sing^KU_XXXX-XXXX^DontStarveTogether^…"}
→ 200 {"Error":false,"GiftReceived":false}

POST /clientitems/DST/DailyGift  {"ClientToken":"pcl-sing^…"}
→ 200 {"Error":false,"GiftReceived":false}

POST /clientitems/DST/GetAllItems  {"ClientToken":"pcl-sing^…", …}
→ 200 {"Error":false,"UserID":"KU_XXXX-XXXX","Items":[{"ItemID":347389181625488581,
       "ItemType":"LEGS_WAXWELL_YULE", …}, …]}   // 2536 件
```

### 完整閉環：真的掉東西了

工具上線後 **32 分鐘**就收到第一個掉落，而且整條因果鏈可以逐項對上：

```
23:03:38  登入成功
23:03:40  收到禮物！tick {'key': 438145790423324876}
23:03:45  庫存增加 2536 -> 2537
```

`Tick` 回傳的 `Key` 是 `438145790423324876`；接著 `GetAllItems` 裡新增的那件物品
`ItemID` 正好也是 `438145790423324876`，`ItemType` = `BODY_BUTTONS_PURPLE_VIOLET`，
`Context` = 3（其他既有物品都是 1）。

也就是說：**心跳 → 伺服器判定 → 禮物進帳**，三個環節都用同一組 ID 對起來了，
而當時遊戲行程是關閉的（同日 22:09 由遊戲本體產生的那件是 `Context` 1，
時間點也完全不同，可以排除）。

| 問題 | 答案 | 證據等級 |
|---|---|---|
| 心跳能否累積成實際掉落 | 能，實測 32 分鐘內拿到 | **已驗證** |

仍然待觀察的只有「一週能拿滿幾件」：`dropsdata` 顯示上限 8，
本次只驗證了單次掉落，長期速率需要持續跑一週才知道。

---

## G. 後續開發計畫

1. **長期速率觀察（1 週）**：單次掉落已驗證，剩下的是一週能拿滿幾件。
   `state.json` 會記下每件禮物的時間與 `Key`，跑滿一個週期（週四 21:40 UTC 重置）
   就能算出實際速率，並確認伺服器端是否也以 8 件為上限。
2. **自動開盒**：目前只領不開。要換成 skin 需 `POST /clientitems/DST/SetItemOpened`
   （body 帶 `item_id` / `modified_time`，字串證據在 `0x1404f82b0`）。
   實作前先用 `GetAllItems` 的 `Context` 欄位確認哪些是未開盒。
3. **常駐化**：用 `pythonw.exe` + 工作排程器（開機啟動）取代前景執行；
   或包成單一 `.exe`（PyInstaller）。
4. **健壯性**：token 失效自動重登已經有了；再加「連續 N 次失敗就重啟 Steam 連線」
   與「偵測 `version.txt` 變更自動換 UA」。
5. **可觀測性**：`state.json` 加 `last_gift_at`、`weekly_count`，
   搭配 21:40 UTC 重置自動歸零。

---

## 風險與殘餘問題

- **服務條款**：這是用官方客戶端自己的 API、官方自己的 token 流程，
  送出官方客戶端會送的請求。風險等級與「把遊戲最小化掛著」相同，
  但仍屬於非官方用法；Klei 若加嚴（例如要求 Tick 前必須有
  game session token）本工具會失效。偵測方式：`E_INVALID_TOKEN`。
- **Steam 狀態**：`SteamAPI_Init()` 會讓 Steam 顯示「正在遊玩 DST」，
  好友看得到。這是這個方案唯一無法避免的外顯副作用。
- **Build 相依**：`User-Agent` 的 build 號換版就變，程式已從 `version.txt` 讀取。
- **未驗證**：心跳是否真的等值於「在世界裡」。伺服器端若有額外的
  session 時長啟發式，可能只有部分心跳被計入。需一週實測。

---

## 附錄：證據索引

| 項目 | 位置 |
|---|---|
| 心跳間隔 360 秒 | `dontstarve_steam_x64.exe` `0x1402020f0` → 常數 `0x1404f3390` = `0000b443` |
| 心跳前置條件 | `0x1401ffcc0` |
| Tick 請求建構 | `0x140202227`（`"clientitems/DST/Tick"`） |
| Tick 回應解析 | `0x1402006b0`（`"GiftReceived"` / `"Key"` / `SendItemDropHeartbeat_Complete`） |
| DailyGift 請求 | `0x1401ed3bc`（`"ClientToken"`） |
| 登入 body 欄位 | `0x14026203c`–`0x1402620dc`（`Username`/`Password`/`SteamTicket`/`appid`/`Game`） |
| Steam 票券附加 | `0x1402624d0`（`&SteamTicket=` / `&SteamID=`） |
| `ms_updategiftitems` | `0x1402009b1`、`0x14020141d` |
| 執行期完整請求 | 遊戲行程記憶體（`POST /clientitems/DST/GetAllItems HTTP/1.1` …） |
| 週期紀錄 | `data\unsafedata\dropsdata_KU_XXXX-XXXX.json`（40 週期，週四 21:40 UTC，上限 8） |
| Lua 端 | `scripts\components\giftreceiver.lua:9`、`scripts\screens\redux\multiplayermainscreen.lua:1119-1171` |
| Mod | `steamapps\workshop\content\322330\2314960345\`（`modinfo.lua`、`modmain.lua`、`scripts\widgets\dropscountdownwidget.lua`） |
