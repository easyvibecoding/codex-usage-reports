<p align="center">
  <img src="../../docs/assets/hero.png" alt="Codex Usage Reports — 每個 Task，每一輪的用量。" width="100%">
</p>

# Codex Usage Reports

**自動產生每個 Codex Task 與每一輪的 Token 用量報告。** 在精簡的本機報告中查看 Task 累計用量、單輪增量、實際觀測到的模型、推理強度與子代理用量。

[![CI](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml/badge.svg)](https://github.com/easyvibecoding/codex-usage-reports/actions/workflows/ci.yml)
[![MIT 授權](https://img.shields.io/badge/license-MIT-mintcream)](../../LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)](../../pyproject.toml)

[English](../../README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

[快速開始](#快速開始) · [報告範例](#看看報告) · [功能與界線](#看清楚每一輪用了多少) · [文件索引](../README.md)

## 快速開始

需要 Python 3.10+，以及支援外掛 hooks 的本機 Codex 環境。行內卡片需要支援本機視覺化的桌面介面；CLI 使用者可閱讀已儲存的報告。原生資料結構會隨版本不同而變動，請參閱[相容性與驗證](../../docs/VALIDATION.md)。

### 1. 安裝外掛

```sh
codex plugin marketplace add easyvibecoding/codex-usage-reports
codex plugin add codex-usage-reports@codex-usage-reports
```

在 Codex 中檢視並信任外掛 hooks，然後**開啟新的 Task**。已安裝的 hooks 會依執行環境的信任與生命週期規則載入。請參閱官方[外掛指南](https://learn.chatgpt.com/docs/plugins)與 [hooks 指南](https://learn.chatgpt.com/docs/hooks)。

若更新變更了 hook 定義，請在 Codex CLI 輸入 `/hooks`，重新檢視並信任所有標示為已變更或未信任的定義，包含新增的 `SubagentStart`。信任綁定的是確切的 hook 定義：外掛即使已安裝並啟用，狀態為 `modified` 的 hooks 仍會被略過。重開 App 或從手機開啟 Task 都不會自動取得信任。完成檢視後，再開啟新的 Task。

### 2. 照常工作

像平常一樣請 Codex 處理工作。支援的生命週期事件會讓主 Task 與各子智能體以自己的 Task／turn 身分記錄基準值、在最終回答前產生卡片，並儲存各自的報告紀錄。自動報告預設啟用；實際涵蓋仍取決於 hook 傳遞與原生紀錄。

自動卡片指示只適用於回答末尾的卡片。若最終回答必須完全符合指定文字、只能輸出 JSON／程式碼，或遵循回答 schema，就略過預覽與參照；產出檔案的格式不影響此判斷。允許附卡片時，主 Task 與子智能體各自只在獨立一行附上自己的原始參照，不轉貼其他代理的參照。詳見[自動報告](../../docs/USAGE.md#automatic-reports)。

子代理沿用父 Task 的預覽目錄時，程式會自動改存子代理自己的目錄。若沙盒拒絕該次寫入，同一張快照可改存子代理工作目錄下的 `work/codex-usage-cards`。既有卡片保持原樣；新版 runtime 由新 Task 載入。

`Stop` 或 `SubagentStop` 之後，一個本機 Python 背景程序會檢查該 Task、該輪的原生 `task_complete` 紀錄，最多進行八次有範圍限制的掃描，重試期限為 25 秒。它不會呼叫模型，也不會延續 Task。確認該輪的完成邊界後，會另存修訂報告，納入已寫入的最終回答用量，避免算入下一輪。證據缺漏或不完整時，仍保留待更新或部分可用狀態；停用自動報告也會停止後續核對。

行內卡片仍是最終回答**之前**擷取的快照。原始終止事件 JSON、HTML 與 Markdown 報告都會保留；重新查詢 Task 報告時，會選用最新發布的修訂版。覆寫卡片的 HTML 檔案無法可靠地更新原卡片：手機遠端 A/B 實驗中，重進 Task 後，原卡片仍顯示 A，新引用才顯示 B。因此，本外掛不啟用原行內卡片的自動替換。請參閱[結束後核對與預覽行為](../../docs/ARCHITECTURE.md#completion-reconciliation)。

### 3. 查看 Task

使用隨附的 `usage-report` skill，例如：

> 顯示這個 Task 的用量報告，包含每輪用量與實際觀測到的設定。

也可以複製儲存庫，使用獨立 CLI：

```sh
git clone https://github.com/easyvibecoding/codex-usage-reports.git
cd codex-usage-reports
python3 plugins/codex-usage-reports/scripts/usage_reports.py auto-report status
python3 plugins/codex-usage-reports/scripts/usage_reports.py task "$TASK_ID" --format markdown
```

將 `TASK_ID` 設為要查看的主 Task 或子智能體的原生 ID。所選 Task 的原生計數與報告紀錄屬於它自己；已驗證的後代用量另列小計。HTML 匯出、設定與解除安裝方式，請參閱[完整使用教學](../../docs/USAGE.md)。

## 看清楚每一輪用了多少

一個長時間執行的 Task 可能包含多輪對話、模型切換與子代理。單一工作階段的總量無法說明最近一輪的變化。Codex Usage Reports 分別呈現這些範圍：

| 想知道什麼？ | 報告會顯示什麼？ |
| --- | --- |
| 這個 Task 累計用了多少？ | 所選主 Task 或子智能體自身實際觀測到的累計 Token 用量。 |
| 這一輪增加了多少？ | 原生本輪計數，或相同來源的有效累計計數差額。 |
| 用了哪個模型與推理強度？ | 該輪觀測到的設定，包含可見的設定變更。 |
| 後代智能體用了多少？ | 依原生親子關係核實的獨立小計，以及資料涵蓋狀態；不併入所選 Task 的原生計數。 |
| 帳號還剩多少配額？ | 若有可用資料，顯示原生配額觀測值，並與 Task Token 用量分開呈現。 |
| 之後還能查閱嗎？ | 留存於本機的 HTML、Markdown 與 JSON 報告紀錄。 |

子智能體的卡片與報告紀錄描述它自己的用量；主 Task 另依可核實的原生譜系彙總後代。主 Task 的後代列與子智能體自己的卡片、報告使用相同的雜湊 `@` 識別碼，同名智能體也能對應。兩邊的觀測時間與涵蓋範圍可能不同，缺少 hook、計數或親子證據時會顯示部分可用或未知，不會把子用量塞進主 Task 的原生計數。

執行階段僅使用 Python 標準函式庫，不需呼叫 LLM 計算用量、不需 API 金鑰，也不會將報告傳送至託管的分析服務。本專案從 [Codex Run Budget](https://github.com/easyvibecoding/codex-run-budget) 抽出報告功能，獨立運作。

## 看看報告

![每輪自動報告，顯示 Task 累計 Token 與本輪增量](../../docs/examples/turn-en.png)

*此範例使用合成資料，由實際報告範本產生，並放在獨立的文件展示框中。桌面版會套用自己的外圍主題。*

<details>
<summary>繁體中文範例</summary>

![繁體中文每輪報告範例](../../docs/examples/turn-zh-Hant.png)

</details>

[開啟範例集](../../docs/examples/README.md)，下載 HTML、查看完成後的報告紀錄與指定 Task 報告。品牌插畫使用 Codex 生圖製作；用量截圖則由合成測試資料實際渲染。[美術提示詞](../../docs/assets/PROMPTS.md)。

## 可以查核的報告

- **如實呈現缺漏資料。** 計數器重設、截斷紀錄與設定衝突，都會保留為部分可用或未知。
- **保留歷史設定。** 目前的全域模型偏好不會覆蓋過去某輪的觀測結果。
- **核對完成紀錄。** 本機背景程序會在有限次數內核對稍後寫入的原生紀錄，並保留原始終止事件 JSON／HTML／Markdown 報告與行內快照。
- **區分統計範圍。** 快取輸入屬於輸入用量的一部分；推理輸出屬於輸出用量的一部分。子代理用量與帳號配額各自呈現。
- **快取讀取占比。** 卡片與儲存的報告會顯示快取輸入占已觀測輸入的比例。缺漏或零輸入維持未提供；這不是官方的快取未命中診斷。
- **資料留在本機。** 報告狀態中的原生識別碼會經過雜湊處理；私人顯示名稱可能出現在你的本機報告中。
- **輕量 hooks。** 報告出錯不會拒絕工具呼叫或停止代理。
- **多語系卡片。** 支援英文、繁體中文、簡體中文、日文、韓文、德文、法文、西班牙文與葡萄牙文；README 提供四種語言版本。

## 專案 exec 活動

依工作目錄查詢額外啟動的 `codex exec`、以前景模式監看變化，或用選用的啟動器保留 ephemeral 執行的開始、退出與用量紀錄。信任相關 hooks 並開啟新任務後，會在工具返回時提示新活動。啟動器歸屬與原生子代理關係分開，exec 用量不自動併入主任務。[指令與涵蓋範圍](../EXEC_ACTIVITY.md)。

## 自動更新與一次授權

首次安裝這版後，在 CLI 輸入 `codex` → `/hooks`，檢視並信任固定入口。之後一般執行程式與 CLI 更新沿用相同的 hook 定義，不必再授權；這表示同意執行同一發布者未來簽署的程式。預設在送出提示時啟動背景檢查，每六小時最多一次，只有簽章與檔案雜湊都通過的版本才會套用。新任務使用新版並顯示已驗證版本，進行中的任務保留原版。

新增 hook、變更入口或金鑰，以及外掛／skill 結構更新，仍須更新外掛並檢視已變更的 hooks。工具不會修改 Codex 的信任紀錄。在已安裝外掛目錄執行 `python3 scripts/publisher_updates.py status` 可讀取實際版本；將 `status` 換成 `off`、`on`、`update` 或 `rollback` 可停用、啟用、手動更新或回退。Plugins 頁面可能仍顯示原安裝版本。[更新機制與操作](../SIGNED_UPDATES.md) · [授權提醒](../UPDATE_NOTICES.md)。

手動檢查已安裝外掛版本與原生 hook 信任狀態時，使用 `python3 scripts/usage_reports.py updates check --refresh --cwd "$PROJECT_DIR"`。此結果與目前啟用的簽署執行程式版本是不同範圍。

## 配對專案變更審查

Codex Run Budget 的跨專案審查是**實驗性、選用功能**。使用者自行指定兩個本機 Codex 專案根目錄建立具名配對，並開啟總開關及各組開關；同一專案可以加入多組配對。對本專案這組配對，啟用後的 Stop hook 觀察到新的遠端 `main` 範圍時，可要求在另一個專案建立唯讀審查 Task。兩個 Task 在該組內一對一綁定，後續 Stop 摘要送往同一個對端，並抑制訊息回聲。[此兩個 repo 的審查契約](../CROSS_REPO_REVIEW.md)要求以證據判斷是否需要對齊。配對及選用的手動遠端掃描由 Run Budget 管理；Usage Reports 仍獨立運作，僅提供報告。[設定與開關說明](https://github.com/easyvibecoding/codex-run-budget/blob/main/docs/PAIRED_REVIEW_AUTOMATION.md)。

## 從 Codex Run Budget 移轉

兩個外掛各自獨立。如果保留原外掛的預算控制功能，請先停用原外掛的自動報告，再啟用本外掛，以免出現重複卡片。不需要遷移歷史資料庫。[移轉說明](../../docs/MIGRATION.md)。

## 限制

這些報告記錄實際觀測到的用量，**不等同帳單或精確費用**。配額屬於帳號，無法單靠 Token 總量分攤到個別 Task。子代理用量的歸屬取決於可取得的原生父子關係資料。Hook 是否送達與原生資料結構都可能隨 Codex 版本而變動。本外掛不設預算限制，也不會中斷工作。

報告可能透露專案名稱與使用模式。請將真實報告保留在本機；公開 issue 僅使用合成測試資料。[隱私與安全](../../SECURITY.md)。

## 文件索引

[文件索引](../README.md)按使用方式整理 CLI、用量數字、exec 活動、簽署更新、排解問題與維護文件。[使用教學](../USAGE.md)列出完整指令；[數字如何計算](../METRICS.md)說明觀測與不完整狀態；[疑難排解](../TROUBLESHOOTING.md)處理卡片與 hooks 問題。

## 參與貢獻

請參閱 [CONTRIBUTING.md](../../CONTRIBUTING.md)、[架構](../../docs/ARCHITECTURE.md)與[驗證方式](../../docs/VALIDATION.md)。歡迎提供精簡且可重現的錯誤回報，以及原生資料結構的相容性修正。請勿附上真實對話紀錄或資料庫。

MIT © EasyVibeCoding contributors。獨立社群專案，與 OpenAI 無隸屬關係，亦未獲其背書。[來源與授權說明](../../NOTICE.md)。
