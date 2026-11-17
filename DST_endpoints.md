# DST 用戶端對外端點清單

- 來源：`bin64\dontstarve_steam_x64.exe`（版號 762172，SHA-256 `08b254abad063db3943bf3f2c013d248f1eb500e32bf924e064843b9f3b3ba34`，x86-64 PE）
- 方法：REA 綁定目標後，直接掃 `.rdata` 字串並對照 `.text` 的 RIP-relative `lea` 參照反組譯；HTTP 層是內嵌 libcurl 的 `HttpClient2`（標頭 `Authorization: Bearer`、`Content-Type: application/json`、`Accept: application/json`、`X-Quiet-Backend-Errors`）
- 日期：2026-11-17

標記說明：
- ✅ 實測打過，HTTP 200 且 `"Error":false`
- ⚙️ 反組譯確認（該路徑被程式碼實際引用；多數另有 request builder 或 `post body` 記錄字串佐證）
- ❓ 只在字串表看到，用途靠命名與周邊字串推斷

表格「依據」欄的位址是 `.text` 內參照該字串的指令 RVA（image base `0x140000000`），不是字串本身的位址。

> 注意：Klei 的 API 一律回 HTTP 200，錯誤放在 body（例如 `{"Error":true,"ErrorCode":"E_INVALID_TOKEN"}`）。判斷成敗要看 body。

## 1. 認證 — `https://login.kleientertainment.com`

| 方法 | 路徑 | 用途 | 依據 |
|---|---|---|---|
| POST | `/login/LoginViaSteam` | 用 Steam 授權票換 Klei token | ✅ 實測 200，回 `{"Token":"pcl-sing^...","UserID":"...","AnalyticsJWT":"..."}` |
| POST | `/login/RegisterSteamAccount` | 首次把 Steam 帳號註冊成 Klei 帳號 | ⚙️ body 建構點 0x261F20（含 `appid:"322330"`） |
| POST | `/login/ServerLoginTokenViaSteam` | 伺服器端用的 Steam 登入票 | ⚙️ 0x261B66 |
| POST | `/login/Login` | 帳密登入（非 Steam 帳號） | ⚙️ body：`Username`、`Password`、`Game`、`FriendlyHostDescriptor`、`TokenExpiry` |
| POST | `/login/CreateAccount` | 建立帳號 | ⚙️ 端點表 0x2290CE |
| POST | `/login/Logout` | 登出 | ⚙️ |
| POST | `/login/LogoutAll` | 登出所有裝置 | ⚙️ |
| POST | `/login/TokenPurpose` | 產生限定用途的 token（錯誤碼 `E_WRONG_TOKEN_PURPOSE`） | ⚙️ |
| POST | `/login/ChangePassword` | 改密碼 | ⚙️ |
| POST | `/login/RegisterGameKey` | 註冊遊戲序號 | ⚙️ |
| POST | `/login/RecoverPassword` | 忘記密碼 | ⚙️ |
| POST | `/login/ReportUser` | 檢舉玩家 | ⚙️ |
| POST | `/login/GameSessionToken` | 進遊戲用的 session token（伺服器以 `ValidateGameSessionToken` 驗） | ⚙️ 0x229116 |
| GET | `/login/auto/?Game=DontStarveTogether` | 導去 `accounts.klei.com` 的自動登入頁 | ❓ |

`LoginViaSteam` body：

```json
{"SteamTicket":"<Steam 授權票，逐位元組 %02X 大寫 hex>",
 "Game":"DontStarveTogether",
 "FriendlyHostDescriptor":"<電腦名>",
 "NoEmail":true}
```

## 2. 物品／庫存 — `https://items.kleientertainment.com`

欄位名注意：`Tick` 用 `Token`，`GetAllItems` 用 `ClientToken`，兩者是同一個 Klei token。

| 方法 | 路徑 | 用途 | 依據 |
|---|---|---|---|
| POST | `clientitems/DST/Tick` | 心跳，累積遊玩時數 → 發禮物 | ✅ 實測 200 `{"Error":false,"GiftReceived":false}` |
| POST | `clientitems/DST/GetAllItems` | 抓完整庫存 | ✅ 實測 200，body 需 `ClientToken` |
| POST | `clientitems/DST/DailyGift` | 每日禮物（回應 `GiftReceived`，觸發 `SetDailyGiftItem` 彈窗） | ⚙️ 0x1ED3BC |
| POST | `clientitems/DST/SetItemOpened` | 標記物品已開啟 | ⚙️ 0x1E3375 |
| POST | `clientitems/DST/ValidateItems` | 驗證庫存內容 | ⚙️ 0x1DD3C3 |
| POST | `clientitems/DST/ValidateSignature` | 驗簽章 | ⚙️ 0x1EE853 |
| POST | `clientitems/DST/ReportUnknownItemType` | 回報未知物品（給官方補資料） | ⚙️ 0x1DC50F |
| POST | `clientitems/DST/GrantSkinDLCEntitlements` | 領取 DLC／造型授權（回應 `AllEntitlements`、`NewEntitlements`） | ⚙️ 0x1ED8E4 |
| POST | `clientitems/DST/VerifyGiftingReceipt` | 驗證送禮收據（body `Receipts`） | ⚙️ 0x2024B4 |
| POST | `clientitems/DST/OpenMysteryBox` | 開神秘箱 | ⚙️ 0x20BD84，有 `OpenBox post body %s` |
| POST | `clientitems/DST/Swap` | 交換物品（body `SwapType`） | ⚙️ 0x20C167 |
| POST | `clientitems/DST/RedeemCode` | 兌換碼（body `Code`） | ⚙️ 0x20B1C4 |
| POST | `clientitems/DST/ReportMission/CROW_GAME` | 小遊戲成績（`Score`、`Moves`） | ⚙️ 0x20B665 |
| POST | `clientitems/DST/ReportMission/CROWKID_GAME` | 小遊戲成績（`Score`、`Steps`） | ⚙️ 0x20B415 |
| POST | `clientitems/DST/ReportMission/SNOWBIRD_GAME` | 小遊戲成績（`Score`、`Eggs`） | ⚙️ 0x20B889 |
| POST | `clientitems/DST/ReportMission/REDBIRD_GAME` | 小遊戲成績（`Score`、`Rotten`） | ⚙️ 0x20BB42 |
| POST | `gameevents/DontStarveTogether/KeyValuesSet` | 上傳單一 key-value | ⚙️ 0x1E3A61 |
| POST | `gameevents/DontStarveTogether/KeyValuesSetMulti` | 上傳多筆 key-value | ⚙️ 0x1DCFF2 |
| POST | `gameevents/DontStarveTogether/KeyValuesGetMulti` | 取回多筆 key-value | ⚙️ 0x1EB8E9 |
| POST | `gameevents/DST/GetStatus` | 活動狀態 | ⚙️ 0x1DD622 |

## 3. 內購／交易 — 同一 items 主機（`BeginItemServerRequest`）

| 方法 | 路徑 | 用途 | 依據 |
|---|---|---|---|
| POST | `/iap/DST/GetPricingInfo` | 取得定價 | ⚙️ 0x1EE4ED |
| POST | `/iap/DST/PurchaseCreate` | 建立購買 | ⚙️ 0x1B2150 |
| POST | `/iap/DST/PurchaseComplete` | 完成購買 | ⚙️ 0x1B22BE |
| POST | `/iap/DST/PurchaseWithCurrency` | 用虛擬貨幣購買 | ⚙️ 0x18FDF2，有 `[PurchaseWithCurrency] BeginItemServerRequest post body %s` |
| POST | `/iap/DST/BarterGainItem` | 以物易物取得（`CurrencyName`、`ExpectedBalance`） | ⚙️ 0x20C503 |
| POST | `/iap/DST/BarterLoseItem` | 以物易物失去 | ⚙️ 0x20CB19 |
| POST | `/iap/DST/BarterDuplicateItems` | 分解重複物品 | ⚙️ 0x20CFF7 |

## 4. 大廳／伺服器列表 — `https://lobby-v2.klei.com`

| 方法 | 路徑 | 用途 | 依據 |
|---|---|---|---|
| POST | `/lobby/write` | 上架伺服器 | ❓ 0x26A602 |
| POST | `/lobby/update` | 更新列表資訊 | ❓ 0x26A622 |
| POST | `/lobby/delete` | 下架 | ❓ 0x269B06 |
| POST | `/lobby/keepAlive` | 保持上架（伺服器心跳） | ❓ 0x26A5CD |
| POST | `/lobby/read` | 讀取伺服器列表 | ❓ 0x2644D4、0x26B215、0x26B3E5 |
| POST | `/lobby/getIP` | 取得對外 IP | ❓ 0x264267 |
| GET | `https://lobby-v2-cdn.klei.com/regioncapabilities-v2.json` | 區域能力／延遲查詢表 | ❓ 0x4B6F54 |

另有 `https://lobby-v2-<region>.klei.com` 這種分區主機（字串 `https://lobby-v2-` 0x2504D2）。

## 5. 其他服務

| 端點 | 用途 | 依據 |
|---|---|---|
| `https://motd.klei.com/motd.json/?game=dst` | 佈告欄／活動訊息 | ⚙️ 0x3540E |
| `https://dst.metrics.klei.com/write` | 遙測上報 | ⚙️ 0x16CDC2 |
| `https://clientservice.kleientertainment.com/analysisCL` | 用戶端分析事件 | ⚙️ 0x16CDD3 |
| `https://analytics-staging.office.kleientertainment.com/write` | 測試環境分析 | ❓ |
| `https://games-feedback.klei.com/submit` | 遊戲內意見回饋 | ❓ |
| `http://crashes.klei.ca/submitCrash` | 當機回報（`CrashReporter.exe`） | ⚙️ 字串表 |
| `https://<region>.gsm.events.klei.ca` | 遊戲伺服器管理事件 | ❓ 0x4B6E94 |
| `https://d21wmy1ql1e52r.cloudfront.net/` | CDN（mod／資源） | ⚙️ |
| `https://s3.amazonaws.com/ds-motd/`、`https://ds-motd.s3.amazonaws.com/`、`https://klei-motd.klei.com/` | MOTD 圖文 | ⚙️ |
| `https://s3.amazonaws.com/ds-mod-language/` | mod 翻譯 | ⚙️ |
| `https://s3.amazonaws.com/dstbuilds/` | 版本資訊 | ⚙️ |
| `https://s3-us-west-2.amazonaws.com/kleifiles/` | 檔案下載 | ⚙️ |
| `https://dst-translation-mods.klei.com/` | 翻譯 mod | ⚙️ |
| `https://theforge.kleientertainment.com/` | The Forge 活動 | ⚙️ |
| `http://dontstarve-release.appspot.com/`、`http://dontstarve-dev.appspot.com/` | 舊版 App Engine 後端（推測已停用） | ❓ |
| `https://accounts.klei.com/account/rewards` | 帳號獎勵頁（瀏覽器導向，`&goto=`） | ⚙️ 0x4C37D0 |
| `https://steamcommunity.com/...` | 開工作坊／個人頁 | ⚙️ |

## 6. 已知 body 參數

| 端點 | 參數 |
|---|---|
| `LoginViaSteam` | `SteamTicket`、`Game`、`FriendlyHostDescriptor`、`NoEmail`、`TokenExpiry`（僅在有效時帶） |
| `Login` | `Username`、`Password`、`Game`、`FriendlyHostDescriptor`、`TokenExpiry` |
| `GetAllItems` | `ClientToken`、`InventoryStateToken`、`EventHash`、`CheckTwitchEntitlements` |
| `Tick` | `Token` |
| `OpenMysteryBox` | 箱子 item id |
| `Swap` | `SwapType` |
| `RedeemCode` | `Code` |
| `ReportMission/*` | `Score` + 各遊戲專屬欄位（`Moves`／`Steps`／`Eggs`／`Rotten`） |
| `VerifyGiftingReceipt` | `Receipts` |

## 7. 還沒摸清楚的部分

- 大廳 API（`/lobby/*`）的請求 body 與驗證方式沒有反組譯，只有端點字串。
- `gameevents/*` 的寫入時機（哪些 key 由哪個系統上傳）未追。
- `/iap/*` 的實際主機是依 `BeginItemServerRequest` 推斷與 items 相同，未逐一驗證。
- 心跳要累積多久才會讓 `GiftReceived` 變 `true`，靜態分析看不出來，只能掛著觀察。
