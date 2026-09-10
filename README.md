# 急診國際年會自動追蹤

每週自動擷取七個急診醫學會的**年會日期地點**與**摘要投稿死線**，產出一頁
儀表板、一個帶提醒的行事曆，以及 CSV／JSON。

| 學會 | 年會 | 來源結構 | 信心 |
|---|---|---|---|
| ACEP | Scientific Assembly | 專屬 Future Dates 頁，列到 2032 | 高 |
| SAEM | Annual Meeting | 專屬 Future Meetings 頁 | 高 |
| IFEM | Global Congress / ICEM | about_congress 頁 | 高 |
| EuSEM | European EM Congress | eusemcongress.org | 中 |
| ASEM | Asian Conference（ACEM，兩年一次） | events 頁列 1–14 屆 | 高 |
| SEMS | Annual Scientific Meeting | WordPress REST API 篩貼文 | 中 |
| HKCEM | 學術活動 | WordPress REST API 篩貼文 | 低 |

## 檔案

```
em_conferences.py    ACEP／SAEM／IFEM／EuSEM 的年會爬蟲
asia_societies.py    ASEM／SEMS／HKCEM 的年會爬蟲
deadlines.py         摘要投稿死線的爬蟲（四個學會各一套解析策略）
render.py            產生儀表板 HTML 與 .ics 行事曆
track.py             排程進入點：爬取 → 比對變動 → 產出
test_parsers.py      年會解析器的離線測試（真實頁面文字，不需連網）
test_deadlines.py    死線解析器的離線測試
track.yml            GitHub Actions 設定檔
data/                上一次的結果，用來比對變動
docs/                儀表板與下載檔（GitHub Pages 就指這個資料夾）
docs/assets/         四個學會的 logo，卡片會引用；請一併 commit
```

## 一次性設定

1. 開一個 GitHub repo，把這些檔案放進去，並把 `track.yml` 移到
   `.github/workflows/track.yml`。
2. 本機先跑一次，確認四個學會都抓得到，並產生第一版 `data/` 與 `docs/`：

   ```bash
   pip install requests beautifulsoup4
   python track.py
   ```

3. Commit 後推上去。
4. 到 repo 的 **Settings → Pages**，Source 選 **Deploy from a branch**，
   分支選 `main`、資料夾選 `/docs`。儀表板網址會是
   `https://<你的帳號>.github.io/<repo 名稱>/`。
5. 到 **Actions** 頁面手動按一次 **Run workflow** 確認整條流程沒問題。

## 自動呈現的三種形式

**儀表板**：`docs/index.html`。上方是「下一場」與倒數天數，接著是投稿死線
清單、年度節奏格線（橫軸月份、縱軸年份），最下面是每場會議一張卡片——
學會 logo、會議名稱、日期、地點，整張卡連到該學會的原始頁面。手機上卡片
會自動收成單欄。

四個學會的 logo 放在 `docs/assets/`，由 repo 自行提供，不向對方網站熱連結
（hotlink），避免對方改網址或擋外連時整片破圖。

**行事曆訂閱**：`docs/conferences.ics`。在 Google 日曆選「其他日曆 → 加入
網址」，貼上 `https://<帳號>.github.io/<repo>/conferences.ics`，年會與死線
就會自動出現在你的日曆裡，之後資料更新也會跟著同步。

死線事件帶 **30 天／7 天／1 天三段提醒**，這是整套工具最實際的產出：
不必記得去看網頁，死線會主動來敲門。日期尚未定案的場次，以及信心等級為
「低」的死線，都不會寫進行事曆，避免用假日期誤導你。

**變動通知**：只要年會日期、地點或任何死線跟上次不同，Actions 會開一個
issue 列出差異，GitHub 寄信給你，信裡開頭就是 30 天內的死線清單。沒有
變動就安靜跑完，不會吵你。

倒數提醒走行事曆、變動通知走 issue，兩邊分工。刻意不讓 issue 每週提醒
同一個死線——每週收到一樣的信，兩個月後你就會自動略過它了。

## 死線追蹤的三個現實

**只在當期存在。** ACEP27 的投稿頁面要到 2027 年 3 月才會出現。程式把
「尚未公布」當成正常狀態顯示，而不是錯誤；等日期一出現，就會被偵測為
「已公布」並通知你——這通常才是你真正在等的那個事件。

**一個學會有很多軌。** 光 SAEM27 就有六個死線橫跨四個月：進階工作坊
（9/16）、教學課程（9/23）、創新與 IGNITE!（11/9）、摘要（2027/1/5）、
臨床影像（2027/1/12）。只盯「abstract deadline」會漏掉一半機會。

**結構化程度差很多**，所以每筆死線都標了信心等級：

| 等級 | 意義 | 適用 |
|---|---|---|
| 高 | 頁面有明確的「Submissions Close: 日期」欄位 | SAEM |
| 中 | 從固定句型的敘述句抽取 | EuSEM |
| 低 | 從自由文字猜的，務必人工確認 | ACEP、IFEM |

低信心的項目會在儀表板標記，也不會設行事曆提醒。寧可少提醒一次，也不要
讓你根據一個猜錯的日期去排實驗進度。

## 維護

四個站都是靜態 HTML，`requests` 就夠，不需要 Selenium 或 Playwright。

真正會壞的情況是官網改版。程式對此有兩層防護：

- 某學會解析不出資料時，**不會清空舊資料**，而是沿用上次的內容並標記，
  儀表板不會無聲地變成空白。
- 同時整個 Actions 執行會標成紅色失敗，GitHub 寄信通知你去修 parser。

改 parser 之後，用 `python test_parsers.py` 與 `python test_deadlines.py`
離線驗證，不必反覆連網打擾對方網站。

死線頁面比年會頁面更容易改版，因為每年換一次投稿週期。IFEM 特別麻煩——
congress 官網每年換網域（icem2026.com → ifem2027.my ...），所以程式不寫死，
而是從 ifem.cc/about_congress 的連結動態找出當期網站。爬取間隔預設 2 秒（`em_conferences.py` 的 `POLITE_DELAY`），
`User-Agent` 記得改成你自己的聯絡信箱。

## 目前抓到的死線（2026-09-10）

| 學會 | 軌 | 死線 | 信心 |
|---|---|---|---|
| SAEM27 | Advanced EM Workshops | 2026-09-16 | 高 |
| SAEM27 | Didactics | 2026-09-23 | 高 |
| SAEM27 | Innovations／IGNITE! | 2026-11-09 | 高 |
| SAEM27 | Abstracts | 2027-01-05（11/2 開放） | 高 |
| SAEM27 | Clinical Images | 2027-01-12 | 高 |
| EuSEM | Late-breaking Abstracts | 未公布 | — |
| ACEP27 | Research Forum | 未公布（往例約 3 月開放、6 月截止） | — |
| IFEM | Abstracts | 未公布（會期前 8–10 個月） | — |

## 目前抓到的年會（2026-09）

| 學會 | 場次 | 日期 | 地點 |
|---|---|---|---|
| IFEM | 25th ICEM | 2026-06-09 – 06-13 | Hamburg, Germany |
| EuSEM | EUSEM 2026 | 2026-09-25 – 09-27 | Paris |
| SAEM | SAEM27 | 2027-05-18 – 05-21 | San Francisco, CA |
| IFEM | 26th Global Congress | JUNE 2027（未定） | Malaysia |
| ACEP | ACEP27 | 2027-10-25 – 10-28 | Boston, MA |
| SAEM | SAEM28 | 2028-05-16 – 05-19 | Austin, TX |
| IFEM | 27th Global Congress | JUNE 2028（未定） | Brazil |
| ACEP | ACEP28 | 2028-09-17 – 09-20 | Las Vegas, NV |
| SAEM | SAEM29 | 2029-05-15 – 05-18 | Las Vegas, NV |
| ACEP | ACEP29 – ACEP32 | 2029 – 2032 | Philadelphia／San Francisco／Chicago／Dallas |

投稿與報名死線請以官方公告為準，本工具只追蹤日期與地點。
