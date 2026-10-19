# 建置說明：報到文件分類網頁 v4（Python / Streamlit ＋ 地端視覺模型自動配對）

> **給 Claude 的指示：**
> 1. 請依照本文件，在這台電腦上建立一個本機執行的 Python 網頁工具，並**安裝好執行環境**。
> 2. 第 11 節附有已驗證過的參考程式碼，請**直接照抄建立檔案**，不要自行改寫演算法或參數。
> 3. **`.env` 的值由使用者自己填**：只要從 `.env.example` 複製出空白的 `.env`，**不要填入任何網址、模型名稱或金鑰，也不要把 `.env` 內容印出來**。
> 4. 建好後依第 12 節驗收，再用繁體中文回報結果。
> 5. 若這台電腦已有舊版（v1–v3），直接用本文件的程式檔覆蓋即可。**`.env` 保留不動、不要改它的值**；v4 新增的設定項目沒寫在 `.env` 時會自動用預設值。

## 0. v4 更新內容（相較 v3）

- **模型伺服器改為 Ollama**（建議放在有獨立顯示卡的機器，例如 `gemma4:26b`），不再借用其他專案的 vLLM。程式仍同時支援 vLLM，換伺服器只改 `.env`。
- **支援「會先思考」的模型（Gemma 4）**：
  - 回覆字數上限改成可設定（`LLM_MAX_TOKENS`，預設 1024；v3 寫死 300，Gemma 4 思考後答案可能被截掉）。
  - 解析回覆時會略過 `<think>…</think>` 思考區塊，取最後一個有效的 JSON。
  - 模型只輸出思考、沒有答案時，顯示明確的錯誤：「請調高 LLM_MAX_TOKENS」。
- 新增 `LLM_EXTRA_BODY`（進階，可額外加請求參數，一般留空）。
- 逾時預設改為 120 秒（第一次呼叫要把模型載入顯示卡）。
- `check_llm.py` 改用與網頁相同的請求方式，並顯示回應秒數和更清楚的錯誤提示。
- 切分、分類、檔名規則與 v3 完全相同。

---

## 1. 目的

學校新進人員（研究助理／研究獎助生）報到時，會繳交一疊紙本文件，整疊雙面掃描成 PDF。這個工具要：

1. 讓使用者上傳掃描檔（可一次多個）。
2. 讓使用者貼上名單（學號＋姓名，可附組別），**順序不限**。
3. 自動切出每個人的文件、判斷文件種類，並用**地端視覺模型（例如 vLLM 上的 Gemma 4）**讀手寫姓名／學號，自動對應到名單上的人。
4. 使用者看一眼核對畫面（沒把握的會標黃色），按一個鈕產出 zip。

## 2. 硬性需求

| 項目 | 要求 |
|---|---|
| 執行位置 | 網頁**只在本機執行**，伺服器只綁定 `localhost` |
| 隱私 | 文件含身分證字號。**任何畫面、log、輸出清單都不可顯示或寫出身分證字號** |
| 送給模型的資料 | **只送兩種裁切小圖**：「健康聲明書」姓名欄、「學習指導實施計畫」第 1 頁的班級/學號/姓名欄（兩者都沒有身分證字號），而且只送到 `.env` 設定的地端伺服器。**絕不可送出個資同意書或整頁** |
| 模型介面 | **OpenAI 相容 API**（`/v1/models`、`/v1/chat/completions`），vLLM 和 Ollama 都能用，換伺服器只改 `.env` |
| 未設定模型時 | 網頁照常可用，改成「依名單順序」預設配對 |
| 頁面方向 | **維持原始掃描方向，不可旋轉任何輸出頁面**（使用者明確要求）。程式內部可以為了判斷而轉正圖片，但不影響輸出的 PDF |
| 套件 | `streamlit`、`pypdfium2`、`numpy`、`pillow`、`requests`、`python-dotenv`。不要用 pypdf，Windows 不需裝 poppler |
| Python | 3.10 以上 |

## 3. 輸入文件的結構

每個人的一套文件，前 5 頁順序固定（雙面掃描，中間會有空白背面頁）：

| 順序 | 頁面 | 說明 |
|---|---|---|
| 1 | 【通知】 | 純印刷品，每份完全相同 → **判斷「新的一套開始」** |
| 2 | 員工健康相關資料蒐集同意書 | 有簽名、**身分證字號**、手機 |
| 3 | 新進人員健康聲明書 p1 | 有手寫**姓名**（核對圖與送模型的小圖從這頁裁） |
| 4 | 新進人員健康聲明書 p2 | |
| 5 | 體檢項目表＋QR code | 純印刷品 |

**後段文件**（體檢項目表之後）可能有以下兩種，數量不固定：

| 文件 | 特徵 |
|---|---|
| 學生兼任研究助理與研究獎助生**型態同意書** | 橫式表格，掃描後是側躺的（可能朝左或朝右）。表頭兩欄二擇一勾選：「研究獎助生」（左欄）或「兼任研究助理」（右欄）。**指導教授會在勾選的那一欄蓋紅色印章** |
| 研究獎助生**學習指導實施計畫**（2 頁）＋ **學習指導評量表（附件一）**（1 頁） | 直式表格，3 頁一組。第 1 頁有手寫「學號」「研究獎助生姓名」 |

實際會遇到的情況（都已驗證）：
- 一般人：1 張型態同意書（兼任研究助理）。
- 研究獎助生：1 張型態同意書（研究獎助生）＋ 1 組實施計畫。
- 有人同時有 2 張型態同意書（研究獎助生、兼任研究助理各一）。
- **別人的實施計畫夾在某人那套後面**（例如上一批同學補交的）→ 同一套裡出現第 2 組實施計畫，要依表單上的學號判斷是誰的。

## 4. 輸出格式

```
報到文件分類_YYYYMMDD_HHMM.zip
├─ 組別/學號_姓名/
│   ├─ 姓名-健康.pdf                          個資同意書＋健康聲明書 2 頁
│   ├─ 姓名-型態.pdf                          勾「兼任研究助理」（或判斷不出）的型態同意書
│   ├─ 國科會計畫-姓名-型態.pdf               勾「研究獎助生」的型態同意書
│   ├─ 國科會計畫-姓名-學習指導實施計畫.pdf   實施計畫 2 頁＋評量表 1 頁合併成一份（同一人多組也合併）
│   └─ 姓名-其他.pdf                          認不出的後段頁（正常不會出現）
└─ 分類清單.csv                               組別,學號,姓名,檔名,頁碼（UTF-8 BOM）
```

- 沒有的文件就不產生該檔。名單沒有組別時省略組別那一層。
- 通知、體檢項目表不輸出。
- 一個人可能只有「學習指導實施計畫」而沒有整套文件（補交的情況），照樣建立他的資料夾。

## 5. 名單格式

每行一位，支援 `學號 姓名`、`學號,姓名`、從 Excel 複製的 `組別<Tab>學號<Tab>姓名`（前面可多欄）。
第一個符合 `[A-Za-z]?\d{6,}` 的欄位是學號，最後一欄是姓名，學號前一欄是組別。`Moblie` 自動更正為 `Mobile`。學號或姓名重複要報錯。

## 6. 切分與分類演算法（已用真實掃描檔驗證，請勿改參數）

**A. 切出每一套**

1. 每頁以 `scale=0.25` 灰階渲染。
2. **空白頁：** 灰階 < 160 的像素比例 < `0.003` 即略過（實測空白頁 0.000，內容頁 ≥ 0.010；只透出一點紅色印泥的背面也算空白）。
3. **特徵：** `MinFilter(3)` → 縮成 60×85（BOX）→ `GaussianBlur(1)` → 反相。
4. **相似度：** 正規化相關係數，**允許上下左右各 ±5 像素位移取最大值**。
   ⚠️ 不能整張直接算：掃描稍偏，通知頁之間就會掉到 0.64。加位移容忍後，同種印刷表單 0.91–1.00，不同表單最高約 0.59。
5. 第一張非空白頁當通知頁參考，相似度 ≥ `0.80` 即新一套開頭。
6. `health = pages[1:4]`、`name_page = pages[2]`、後段 `tail = pages[5:]`。

**B. 後段頁分類**

1. **型態同意書（側躺）：** 以 `scale=0.5` 灰階、墨水二值化，分別取「每列墨水量」與「每欄墨水量」的投影，減去 15 點移動平均後算能量。列能量 ÷ 欄能量 < `1.5` 即為側躺的橫式表單（實測型態同意書 0.23–0.75，其他頁 ≥ 3.17）。
2. **勾哪一型：**
   - 先轉正（只用於判斷）：比較左右兩側各 12% 寬的墨水量（上下各去掉 10%），**較密的一側是標題**，把它轉到上方。
   - 找紅色像素（R>120、R−G>50、R−B>30）。紅色 80% 以上在轉正後的**左半 → 研究獎助生**，20% 以下 → **兼任研究助理**，其他或紅色太少（<20 像素）→ 判斷不出，歸為一般「型態」並提醒。
   - ⚠️ 不要用勾選框的墨水量判斷：實測框的位置因掃描偏移而不穩，分不開。紅色印章位置則 100% 正確（16 張全對，左/右比例都是 0.00 或 1.00）。
3. **學習指導實施計畫：** 以文件中**第一張直式後段頁**當參考（＝實施計畫第 1 頁），相似度 ≥ `0.80` 就是新的一組開頭，後面的直式頁接到這組（評量表與第 1 頁相似度約 0.48，所以不會被當成新組）。同一套中出現第 2 組以上標記為 `extra`（可能是別人的）。
4. 頁數不是 3 的組、找不到型態同意書、同型多張、認不出的頁，都顯示黃色提醒。

**驗證結果：** 4 份真實掃描檔（共 15 人、138 頁）全部分類正確：型態同意書 16 張（研究獎助生 4、兼任研究助理 12），學習指導實施計畫 4 組，其中 1 組是夾在別人後面的。

## 7. AI 配對（`ai_match.py`）

**A. 每一套 → 用健康聲明書的姓名**

1. 裁姓名欄（相對座標 `x 0.06–0.55、y 0.155–0.21`，`scale=2.0`）。
2. 小圖連同**名單所有姓名**送給模型，問「是名單上哪一位」（選擇題），回 JSON `{"read","name","confidence"}`。
3. 分數 = 模型選中 `+1` ＋「讀到的字」與姓名的 `difflib` 相似度。由高到低貪婪分配，**同一人不會配給兩套**，分數 < `0.6` 不配。
4. 黃色「請確認」：沒配到、請求失敗、模型選的人≠最後配到的人、信心 < `0.7`。

**B. 學習指導實施計畫 → 優先用表單上的學號**

1. 裁第 1 頁上方（`x 0.08–0.95、y 0.10–0.215`：班級、學號、姓名、指導教師），`scale=2.0`。
2. 連同名單（學號＋姓名）送給模型，回 JSON `{"sid","read","name","confidence"}`。
3. **學號與名單完全相符 → 直接判定**；否則模型選中且姓名相似度 ≥ 0.6 才判定；都不行 → 預設為「所在那一套的人」並標黃色。同一人可以有多組，不做一對一限制。

**共通：**
- 先用 `response_format: json_schema`（`name` 限制在名單＋`UNKNOWN` 的 enum，vLLM 支援）；伺服器回 400/422 時自動改一般模式重送，再從回覆中抓第一個 `{...}` 解析。
- `temperature 0`、`max_tokens` 取自 `.env` 的 `LLM_MAX_TOKENS`（預設 1024）、同時最多 4 個請求。`LLM_EXTRA_BODY` 的 JSON 會合併進每個請求。
- 回覆解析：先移除 `<think>…</think>`，再從每個 `{` 嘗試 `json.JSONDecoder().raw_decode`，取**最後一個**含 `name` 或 `read` 的物件。
- 回覆 `content` 為空 → 拋出錯誤「模型沒有回覆內容」；若訊息中有 `reasoning` / `reasoning_content`，再提示調高 `LLM_MAX_TOKENS`。
- 全部請求都失敗 → 顯示錯誤，改用名單順序預設。

已用模擬伺服器驗證：名單亂序、故意給錯字／放棄／選錯人，仍全部配對正確並正確標黃；夾在別人後面的實施計畫依學號正確歸到本人。

## 8. 網頁流程（Streamlit）

- **側邊欄：** 是否已設定模型、「使用 AI 配對」勾選框、「測試連線」按鈕。
- **1. 掃描檔：** 可多選，依檔名排序後合併處理。
- **2. 名單：** 文字框，即時解析並顯示錯誤。
- **3. 開始分析 → 核對：**
  - 每一套一列：左邊姓名欄裁切圖，右邊下拉選單（預設為 AI 結果），下方顯示型態同意書種類、AI 讀到的字、警示。
  - 「研究獎助生學習指導實施計畫」每組一列：左邊學號/姓名欄裁切圖，右邊下拉選單（預設：AI 依學號判斷的人；沒有 AI 結果時預設為所在那一套的人）。
  - 同一人被選給兩套 → 報錯並停用打包。
- **4. 分類並打包 → 下載壓縮檔。**
- 裁切圖用 `st.cache_data` 快取；用 `width="stretch"`，不要用已棄用的 `use_container_width`。

## 8.5 模型伺服器（給使用者參考，Claude 不需要動手）

建議用 **Ollama** 跑在有獨立顯示卡、且沒有被其他服務佔滿的機器上：

| 模型 | 大小 | 建議 |
|---|---|---|
| `gemma4:26b` | 約 17 GB | **推薦**。24GB 顯示卡放得下；MoE 架構，速度快 |
| `gemma4:31b` | 約 19 GB | 24GB 卡處理圖片時偏緊 |
| `gemma4:e4b` | 約 9.6 GB | 顯示卡較小時的備案 |

伺服器端要讓內網連得到：`sudo ss -ltnp | grep 11434` 應顯示 `0.0.0.0:11434` 或 `*:11434`；若是 `127.0.0.1:11434`，用 `sudo systemctl edit ollama` 加入：

```
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

再 `sudo systemctl restart ollama`。從執行網頁的機器測試：`curl http://<模型伺服器IP>:11434/v1/models`。

⚠️ 不要把模型放在已被其他服務（例如 vLLM）佔滿的顯示卡上：vLLM 啟動時會預約固定比例的顯示記憶體，被佔用後可能無法重啟，影響其他專案。

## 9. 隱私檢查重點

- 送給模型的只有第 7 節兩種裁切小圖。
- `分類清單.csv`、畫面提示、終端機輸出都不含身分證字號。
- `.env` 不可印出，`.gitignore` 要排除 `.env`。

## 10. 要建立的檔案

```
報到文件分類網頁/
├─ app.py              網頁介面
├─ core.py             切分、分類、打包（不依賴 Streamlit）
├─ ai_match.py         地端視覺模型配對
├─ check_llm.py        使用者填好 .env 後，用來測試連線＋看圖
├─ requirements.txt
├─ .env.example        設定範本（值留空）
├─ .env                從 .env.example 複製，值留空，由使用者自己填
├─ .gitignore          排除 .env
├─ .streamlit/config.toml
├─ 啟動.bat            Windows（CRLF 換行）
├─ 啟動.command        Mac / Linux（chmod +x）
└─ 使用說明.txt
```

## 11. 參考程式碼（已驗證，請照抄）

### `core.py`

```python
"""
報到文件分類：核心邏輯（不含網頁介面）

每個人的一套文件順序固定：
    通知 → 個資同意書 → 健康聲明書 p1 → 健康聲明書 p2 → 體檢項目表 → 後段文件
後段文件（體檢項目表之後）可能有：
    - 型態同意書（橫式表格，掃描後側躺）：勾「兼任研究助理」或「研究獎助生」
    - 研究獎助生學習指導實施計畫（2 頁）＋ 學習指導評量表（附件一，1 頁）＝ 一組 3 頁
空白頁（雙面掃描的背面）會自動略過。

判斷方式（都不需要辨識手寫字，完全離線）：
- 空白頁：墨水比例極低。
- 每套的開頭：與第一張「通知」頁外觀相似度很高（印刷品，每份都一樣）。
- 型態同意書：文字行是直的（頁面側躺），用水平/垂直投影判斷。
- 勾哪一型：指導教授會在「所勾選那一欄」蓋紅色印章 → 紅色在左半＝研究獎助生，右半＝兼任研究助理。
- 學習指導實施計畫：與文件中第一張實施計畫第 1 頁的外觀相似度高 → 新的一組開始。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

BLANK_INK = 0.003      # 墨水比例低於此值視為空白頁
NOTICE_SIM = 0.80      # 與參考頁相似度高於此值視為同一種印刷表單
THUMB = (60, 85)       # 比對用縮圖大小
MAX_SHIFT = 5          # 容許掃描位移（縮圖像素）
LANDSCAPE_RATIO = 1.5  # 水平/垂直文字行能量比低於此值 → 側躺的橫式表單（實測：橫式 ≤0.75，直式 ≥3.1）

GRANT = "grant"            # 研究獎助生（國科會計畫）
ASSISTANT = "assistant"    # 兼任研究助理
UNKNOWN = "unknown"


# ---------- 名單 ----------

@dataclass
class Person:
    sid: str
    name: str
    group: str = ""


def parse_roster(text: str) -> tuple[list[Person], list[str]]:
    """
    每行一位。支援：
        1411230001 王小明
        1411230001,王小明
        大四<Tab>AIoT<Tab>1411230001<Tab>王小明   （直接從 Excel 複製）
    規則：數字那一欄是學號，最後一欄是姓名；學號前面的最後一欄當作組別。
    """
    people, errors = [], []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        tokens = [t for t in re.split(r"[\s,，、]+", line) if t]
        sid_idx = next((k for k, t in enumerate(tokens) if re.fullmatch(r"[A-Za-z]?\d{6,}", t)), None)
        if sid_idx is None or sid_idx == len(tokens) - 1:
            errors.append(f"第 {i} 行看不懂：「{line}」（需要 學號 + 姓名）")
            continue
        group = tokens[sid_idx - 1] if sid_idx > 0 else ""
        group = group.replace("Moblie", "Mobile")
        people.append(Person(sid=tokens[sid_idx], name=tokens[-1], group=group))

    seen_sid, seen_name = set(), set()
    for p in people:
        if p.sid in seen_sid:
            errors.append(f"學號重複：{p.sid}")
        if p.name in seen_name:
            errors.append(f"姓名重複：{p.name}")
        seen_sid.add(p.sid)
        seen_name.add(p.name)
    return people, errors


# ---------- PDF 分析 ----------

@dataclass
class TypeForm:
    page: int
    kind: str              # GRANT / ASSISTANT / UNKNOWN


@dataclass
class PlanUnit:
    """一組「學習指導實施計畫 2 頁 + 評量表 1 頁」。"""
    pages: list[int]
    packet: int            # 實體上夾在第幾套裡（0 起算）
    extra: bool = False    # 同一套裡的第 2 組以後 → 可能是別人的，需要確認
    warnings: list[str] = field(default_factory=list)

    @property
    def id_page(self) -> int:   # 實施計畫第 1 頁（有學號、姓名，沒有身分證字號）
        return self.pages[0]


@dataclass
class Packet:
    """掃描檔中一個人的一套文件（頁碼從 0 開始）。"""
    pages: list[int]
    warnings: list[str] = field(default_factory=list)
    type_forms: list[TypeForm] = field(default_factory=list)
    others: list[int] = field(default_factory=list)   # 認不出的後段頁

    @property
    def health(self) -> list[int]:        # 個資同意書 + 健康聲明書 2 頁
        return self.pages[1:4]

    @property
    def tail(self) -> list[int]:          # 體檢項目表之後的頁
        return self.pages[5:]

    @property
    def name_page(self) -> int | None:    # 健康聲明書第 1 頁（有姓名、沒有身分證字號）
        return self.pages[2] if len(self.pages) > 2 else None


def _gray(page: "pdfium.PdfPage", scale: float) -> Image.Image:
    return page.render(scale=scale, grayscale=True).to_pil().convert("L")


def _features(img: Image.Image) -> tuple[float, np.ndarray]:
    arr = np.asarray(img, dtype=np.float32)
    ink = float((arr < 160).mean())
    small = img.filter(ImageFilter.MinFilter(3)).resize(THUMB, Image.BOX).filter(ImageFilter.GaussianBlur(1))
    return ink, 255 - np.asarray(small, dtype=np.float32)


def _sim(a: np.ndarray, b: np.ndarray, s: int = MAX_SHIFT) -> float:
    """容許上下左右小幅位移的相關係數（掃描放歪一點也認得出來）。"""
    h, w = a.shape
    best = -1.0
    for dy in range(-s, s + 1):
        for dx in range(-s, s + 1):
            A = a[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)]
            B = b[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            A = A - A.mean()
            B = B - B.mean()
            n = np.linalg.norm(A) * np.linalg.norm(B)
            if n:
                best = max(best, float((A * B).sum() / n))
    return best


def _is_landscape(page: "pdfium.PdfPage") -> bool:
    """文字行是直的（頁面側躺）→ 橫式的型態同意書。"""
    ink = (np.asarray(_gray(page, 0.5), dtype=np.float32) < 160).astype(np.float32)

    def hf(p):
        p = p - np.convolve(p, np.ones(15) / 15, "same")
        return float((p[10:-10] ** 2).mean())

    r, c = hf(ink.mean(1)), hf(ink.mean(0))
    return c > 0 and r / c < LANDSCAPE_RATIO


def _upright(img: Image.Image) -> Image.Image:
    """把側躺的型態同意書轉正（標題那一側文字較密）。只用於判斷，不影響輸出的 PDF。"""
    ink = np.asarray(img.convert("L")) < 160
    h, w = ink.shape
    col = ink[int(0.1 * h):int(0.9 * h)].mean(0)
    left, right = col[: int(0.12 * w)].sum(), col[-int(0.12 * w):].sum()
    return img.rotate(-90, expand=True) if left > right else img.rotate(90, expand=True)


def _type_kind(page: "pdfium.PdfPage") -> str:
    """紅色印章在轉正後的左半 → 研究獎助生；右半 → 兼任研究助理。"""
    a = np.asarray(_upright(page.render(scale=0.5).to_pil().convert("RGB")), dtype=np.int16)
    red = (a[..., 0] > 120) & (a[..., 0] - a[..., 1] > 50) & (a[..., 0] - a[..., 2] > 30)
    xs = np.nonzero(red)[1]
    if len(xs) < 20:
        return UNKNOWN
    left = float((xs < a.shape[1] / 2).mean())
    if left > 0.8:
        return GRANT
    if left < 0.2:
        return ASSISTANT
    return UNKNOWN


def analyze(pdf_bytes: bytes) -> tuple[list[Packet], list[PlanUnit], dict]:
    pdf = pdfium.PdfDocument(pdf_bytes)
    feats = [_features(_gray(pdf[i], scale=0.25)) for i in range(len(pdf))]
    blanks = [i for i, (ink, _) in enumerate(feats) if ink < BLANK_INK]
    content = [i for i in range(len(pdf)) if i not in blanks]
    info = {"total": len(pdf), "blanks": blanks, "sims": {}}
    if not content:
        return [], [], info

    ref = feats[content[0]][1]               # 第一張非空白頁 = 通知頁
    starts = []
    for i in content:
        sim = _sim(ref, feats[i][1])
        info["sims"][i] = sim
        if sim >= NOTICE_SIM:
            starts.append(i)

    packets = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(pdf)
        pk = Packet(pages=[i for i in content if s <= i < end])
        if len(pk.pages) < 6:
            pk.warnings.append(f"這一套只有 {len(pk.pages)} 頁（正常至少 6 頁），可能缺頁")
        packets.append(pk)

    # 第 5 頁應該是「體檢項目表」（印刷品），用第一套的那頁當參考檢查
    if packets and len(packets[0].pages) > 4:
        exam_ref = feats[packets[0].pages[4]][1]
        for pk in packets[1:]:
            if len(pk.pages) > 4 and _sim(exam_ref, feats[pk.pages[4]][1]) < NOTICE_SIM:
                pk.warnings.append("第 5 頁看起來不是體檢項目表，頁序可能不對")

    # 後段文件：型態同意書（側躺）或 學習指導實施計畫/評量表（直式）
    landscape = {i for pk in packets for i in pk.tail if _is_landscape(pdf[i])}
    portrait = [i for pk in packets for i in pk.tail if i not in landscape]
    plan_ref = feats[portrait[0]][1] if portrait else None   # 第一張直式後段頁 = 實施計畫第 1 頁

    units: list[PlanUnit] = []
    for k, pk in enumerate(packets):
        current: PlanUnit | None = None
        for i in pk.tail:
            if i in landscape:
                kind = _type_kind(pdf[i])
                pk.type_forms.append(TypeForm(i, kind))
                if kind == UNKNOWN:
                    pk.warnings.append(f"第 {i + 1} 頁型態同意書判斷不出勾哪一型，先歸為「型態」，請確認")
                current = None
            elif plan_ref is not None and _sim(plan_ref, feats[i][1]) >= NOTICE_SIM:
                current = PlanUnit(pages=[i], packet=k, extra=any(u.packet == k for u in units))
                units.append(current)
            elif current is not None:
                current.pages.append(i)
            else:
                pk.others.append(i)
        if not pk.type_forms:
            pk.warnings.append("沒有找到型態同意書")
        for kind, label in ((GRANT, "研究獎助生"), (ASSISTANT, "兼任研究助理")):
            n = sum(t.kind == kind for t in pk.type_forms)
            if n > 1:
                pk.warnings.append(f"有 {n} 張勾「{label}」的型態同意書，會合併成同一個檔，請確認")
        if pk.others:
            pk.warnings.append(f"第 {', '.join(str(i + 1) for i in pk.others)} 頁認不出是什麼文件，會另存成「其他」")

    for u in units:
        if len(u.pages) != 3:
            u.warnings.append(f"這組有 {len(u.pages)} 頁（正常是實施計畫 2 頁＋評量表 1 頁），請確認")

    lead = [i for i in content if i < starts[0]] if starts else content
    if lead:
        info["lead"] = lead
    return packets, units, info


def render_crop(pdf_bytes: bytes, page_index: int, box=(0.06, 0.155, 0.55, 0.21), scale=1.5) -> Image.Image:
    """裁出健康聲明書上的姓名區塊，給使用者核對。"""
    pdf = pdfium.PdfDocument(pdf_bytes)
    img = pdf[page_index].render(scale=scale).to_pil()
    w, h = img.size
    return img.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))


PLAN_ID_BOX = (0.08, 0.10, 0.95, 0.215)   # 實施計畫第 1 頁：班級、學號、姓名、指導教師


# ---------- 輸出 ----------

def _safe(text: str) -> str:
    return "".join(c for c in text if c not in '\\/:*?"<>|').strip()


def _extract(src: "pdfium.PdfDocument", pages: list[int]) -> bytes:
    out = pdfium.PdfDocument.new()
    out.import_pages(src, pages)
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def plan_outputs(
    packet_people: list[tuple[Packet, Person]],
    unit_people: list[tuple[PlanUnit, Person]],
) -> dict[tuple[str, str, str], dict[str, list[int]]]:
    """
    回傳 {(組別, 學號, 姓名): {檔名: [頁碼...]}}。頁面維持原始方向。
      姓名-健康.pdf                          個資同意書 + 健康聲明書
      姓名-型態.pdf                          勾「兼任研究助理」（或判斷不出）的型態同意書
      國科會計畫-姓名-型態.pdf               勾「研究獎助生」的型態同意書
      國科會計畫-姓名-學習指導實施計畫.pdf   實施計畫 2 頁 + 評量表 1 頁（同一人多組會合併）
      姓名-其他.pdf                          認不出的後段頁
    """
    out: dict[tuple[str, str, str], dict[str, list[int]]] = {}

    def add(p: Person, fname: str, pages: list[int]):
        if pages:
            files = out.setdefault((p.group, p.sid, p.name), {})
            files.setdefault(fname, []).extend(pages)

    for pk, p in packet_people:
        n = _safe(p.name)
        add(p, f"{n}-健康.pdf", pk.health)
        add(p, f"{n}-型態.pdf", [t.page for t in pk.type_forms if t.kind != GRANT])
        add(p, f"國科會計畫-{n}-型態.pdf", [t.page for t in pk.type_forms if t.kind == GRANT])
        add(p, f"{n}-其他.pdf", pk.others)
    for u, p in unit_people:
        add(p, f"國科會計畫-{_safe(p.name)}-學習指導實施計畫.pdf", u.pages)
    return out


def build_zip(pdf_bytes: bytes, outputs: dict[tuple[str, str, str], dict[str, list[int]]]) -> bytes:
    src = pdfium.PdfDocument(pdf_bytes)
    buf = io.BytesIO()
    summary = ["組別,學號,姓名,檔名,頁碼"]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for (group, sid, name), files in outputs.items():
            folder = "/".join(x for x in (_safe(group), f"{_safe(sid)}_{_safe(name)}") if x)
            for fname, pages in files.items():
                zf.writestr(f"{folder}/{fname}", _extract(src, pages))
                summary.append(f"{group},{sid},{name},{fname},{' '.join(str(x + 1) for x in pages)}")
        zf.writestr("分類清單.csv", "﻿" + "\n".join(summary) + "\n")
    return buf.getvalue()
```

### `ai_match.py`

```python
"""
用地端視覺模型（vLLM / Ollama 的 OpenAI 相容 API）自動配對手寫姓名。

只會把「健康聲明書姓名欄」的小圖送到 .env 設定的伺服器，
不會送出個資同意書（有身分證字號）或任何其他頁面。
"""
from __future__ import annotations

import base64
import difflib
import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image

CONFIDENT = 0.7      # 模型自評信心低於此值 → 標記「請確認」
MIN_SCORE = 0.6      # 配對分數低於此值 → 不配對，留給使用者選
WORKERS = 4          # 同時送出的請求數

PROMPT = """這是一張手寫表單的局部，「姓名：」後面是手寫的中文姓名。
候選名單：{names}
請判斷手寫姓名是名單中的哪一位。只回覆 JSON，不要其他文字：
{{"read": "你看到的手寫字", "name": "名單中最符合的姓名；完全看不出來就填 UNKNOWN", "confidence": 0 到 1 的數字}}"""


def config() -> dict:
    """從環境變數（.env）讀設定。值由使用者自己填。"""
    return {
        "base_url": os.getenv("LLM_BASE_URL", "").strip().rstrip("/"),
        "model": os.getenv("LLM_MODEL", "").strip(),
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "timeout": float(os.getenv("LLM_TIMEOUT", "") or 120),
        # 回覆字數上限。Gemma 4 等「會先思考」的模型，思考內容也算在內，太小會導致答案被截掉
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "") or 1024),
        # 進階：額外加進請求的參數（JSON），例如關閉思考。留空即可
        "extra": _json_env("LLM_EXTRA_BODY"),
    }


def _json_env(key: str) -> dict:
    raw = os.getenv(key, "").strip()
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


def enabled(cfg: dict) -> bool:
    return bool(cfg["base_url"] and cfg["model"])


def _headers(cfg: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        h["Authorization"] = f"Bearer {cfg['api_key']}"
    return h


def list_models(cfg: dict) -> list[str]:
    r = requests.get(f"{cfg['base_url']}/models", headers=_headers(cfg), timeout=10)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def _parse(text: str) -> dict:
    """從回覆中取出 JSON。會略過 <think>…</think> 思考區塊，並取最後一個有效的 JSON 物件。"""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    dec = json.JSONDecoder()
    found = {}
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("name" in obj or "read" in obj):
            found = obj
    return found


def chat(cfg: dict, body: dict) -> str:
    """送出請求並取回文字回覆。伺服器不支援結構化輸出（400/422）時，自動拿掉再送一次。"""
    body = {**body, "model": cfg["model"], "max_tokens": cfg["max_tokens"], **cfg["extra"]}
    url = f"{cfg['base_url']}/chat/completions"
    r = requests.post(url, json=body, headers=_headers(cfg), timeout=cfg["timeout"])
    if r.status_code in (400, 422) and "response_format" in body:
        body.pop("response_format")
        r = requests.post(url, json=body, headers=_headers(cfg), timeout=cfg["timeout"])
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    text = msg.get("content") or ""
    if not text.strip():
        thinking = msg.get("reasoning") or msg.get("reasoning_content")
        hint = "（模型只輸出了思考內容，請調高 .env 的 LLM_MAX_TOKENS）" if thinking else ""
        raise RuntimeError("模型沒有回覆內容" + hint)
    return text


def ask(cfg: dict, img: Image.Image, names: list[str]) -> dict:
    """問模型這張姓名小圖是名單中的誰。"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    schema = {
        "type": "object",
        "properties": {
            "read": {"type": "string"},
            "name": {"type": "string", "enum": names + ["UNKNOWN"]},
            "confidence": {"type": "number"},
        },
        "required": ["read", "name", "confidence"],
    }
    body = {
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(names="、".join(names))},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        # 結構化輸出：限制 name 只能是名單上的人（vLLM 支援；不支援的伺服器會自動退回一般模式）
        "response_format": {"type": "json_schema", "json_schema": {"name": "match", "schema": schema}},
    }
    data = _parse(chat(cfg, body))
    try:
        conf = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {"read": str(data.get("read", "")).strip(), "name": str(data.get("name", "UNKNOWN")).strip(), "confidence": conf}


PROMPT_UNIT = """這是「研究獎助生學習指導實施計畫」表格的上半部，有手寫的「學號」和「研究獎助生姓名」。
候選名單（學號 姓名）：{roster}
請讀出學號和姓名，並判斷是名單中的哪一位。只回覆 JSON，不要其他文字：
{{"sid": "你讀到的學號", "read": "你讀到的姓名", "name": "名單中最符合的姓名；完全看不出來就填 UNKNOWN", "confidence": 0 到 1 的數字}}"""


def ask_unit(cfg: dict, img: Image.Image, people: list[tuple[str, str]]) -> dict:
    """people: [(學號, 姓名), ...]"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    names = [n for _, n in people]
    schema = {
        "type": "object",
        "properties": {
            "sid": {"type": "string"},
            "read": {"type": "string"},
            "name": {"type": "string", "enum": names + ["UNKNOWN"]},
            "confidence": {"type": "number"},
        },
        "required": ["sid", "read", "name", "confidence"],
    }
    body = {
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT_UNIT.format(roster="、".join(f"{s} {n}" for s, n in people))},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "response_format": {"type": "json_schema", "json_schema": {"name": "unit", "schema": schema}},
    }
    data = _parse(chat(cfg, body))
    try:
        conf = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "sid": re.sub(r"\s", "", str(data.get("sid", ""))),
        "read": str(data.get("read", "")).strip(),
        "name": str(data.get("name", "UNKNOWN")).strip(),
        "confidence": conf,
    }


def match_units(cfg: dict, crops: list[Image.Image], people: list[tuple[str, str]]) -> tuple[list[int | None], list[dict]]:
    """
    學習指導實施計畫：優先用「學號完全相符」判斷是誰（最可靠），其次才看姓名。
    同一人可以有多組，所以不做一對一限制。
    回傳 (assigned, details)；details[k]["by"] = "sid" / "name" / None
    """
    def run(img):
        try:
            return ask_unit(cfg, img, people)
        except Exception as e:
            return {"sid": "", "read": "", "name": "UNKNOWN", "confidence": 0.0, "error": str(e)[:200]}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(run, crops))

    sids = [s for s, _ in people]
    names = [n for _, n in people]
    assigned, details = [], []
    for r in results:
        j, by = None, None
        if r["sid"] in sids:
            j, by = sids.index(r["sid"]), "sid"
        else:
            scores = [_score(r, n) for n in names]
            if scores and max(scores) >= MIN_SCORE + 1.0:   # 必須模型選中且姓名相近
                j, by = int(np.argmax(scores)), "name"
        uncertain = j is None or bool(r.get("error")) or (by == "name" and r["confidence"] < CONFIDENT)
        assigned.append(j)
        details.append({**r, "by": by, "uncertain": uncertain})
    return assigned, details


def _score(res: dict, name: str) -> float:
    """模型選中 +1；再加上「讀到的字」和姓名的相似度（0~1）。"""
    s = difflib.SequenceMatcher(None, res["read"], name).ratio()
    return s + (1.0 if res["name"] == name else 0.0)


def match(cfg: dict, crops: list[Image.Image], names: list[str]) -> tuple[list[int | None], list[dict]]:
    """
    回傳 (assigned, details)
      assigned[k] = 第 k 套配到的名單索引（None = 沒配到）
      details[k]  = {"read", "name", "confidence", "uncertain", "error"}
    同一人不會被配給兩套（分數高的優先）。
    """
    def run(img):
        try:
            return ask(cfg, img, names)
        except Exception as e:  # 單張失敗不影響其他張
            return {"read": "", "name": "UNKNOWN", "confidence": 0.0, "error": str(e)[:200]}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(run, crops))

    pairs = sorted(
        ((_score(r, n), k, j) for k, r in enumerate(results) for j, n in enumerate(names)),
        reverse=True,
    )
    assigned: list[int | None] = [None] * len(results)
    used = set()
    for s, k, j in pairs:
        if s >= MIN_SCORE and assigned[k] is None and j not in used:
            assigned[k] = j
            used.add(j)

    details = []
    for k, r in enumerate(results):
        j = assigned[k]
        uncertain = (
            j is None
            or r.get("error")
            or r["name"] != names[j]
            or r["confidence"] < CONFIDENT
        )
        details.append({**r, "uncertain": bool(uncertain)})
    return assigned, details
```

### `app.py`

```python
"""
新進人員報到文件 分類網頁

啟動：streamlit run app.py
掃描檔只在這台電腦處理；啟用 AI 配對時，只有「姓名欄 / 學號欄小圖」會送到 .env 設定的地端模型伺服器。
"""
import hashlib
import io
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium
import streamlit as st
from dotenv import load_dotenv

import ai_match
import core

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
cfg = ai_match.config()

st.set_page_config(page_title="報到文件分類", page_icon="📁", layout="centered")
st.title("報到文件分類")
st.caption("丟入掃描檔 → 貼上名單 → 分析 → 下載壓縮檔。")

KIND_LABEL = {core.GRANT: "研究獎助生", core.ASSISTANT: "兼任研究助理", core.UNKNOWN: "判斷不出"}
SKIP = "（略過）"


def merge_pdfs(files) -> bytes:
    """多個掃描檔依順序合併成一份再處理。"""
    if len(files) == 1:
        return files[0].getvalue()
    out = pdfium.PdfDocument.new()
    for f in files:
        out.import_pages(pdfium.PdfDocument(f.getvalue()))
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=500)
def crop(key: str, page: int, box: tuple, _pdf: bytes):
    return core.render_crop(_pdf, page, box=box)


# ---------- 側邊欄：AI 配對設定 ----------
with st.sidebar:
    st.header("AI 自動配對")
    if ai_match.enabled(cfg):
        st.success(f"已設定模型：{cfg['model']}")
        use_ai = st.checkbox("使用 AI 配對", value=True)
        if st.button("測試連線"):
            try:
                models = ai_match.list_models(cfg)
                if cfg["model"] in models:
                    st.success("連線成功，模型可用。")
                else:
                    st.warning("連得到伺服器，但找不到這個模型。伺服器上有：" + "、".join(models))
            except Exception as e:
                st.error(f"連不到伺服器：{e}")
    else:
        use_ai = False
        st.info("尚未設定。請在 .env 填入 LLM_BASE_URL 和 LLM_MODEL 後重新啟動。未設定時會依名單順序預設配對。")

# ---------- 1. 掃描檔 ----------
st.subheader("1. 掃描檔")
files = st.file_uploader("可一次丟多個 PDF，會依檔名順序處理", type="pdf", accept_multiple_files=True)

# ---------- 2. 名單 ----------
st.subheader("2. 名單")
roster_text = st.text_area(
    "每行一位：學號 姓名（可直接從 Excel 複製「組別 學號 姓名」貼上）",
    height=220,
    placeholder="AIoT\t1411230001\t王小明\nMobile\t1411230002\t陳小華\n1811530001 林小美",
)
st.caption("有組別時會多一層組別資料夾。沒有啟用 AI 配對時，名單照掃描順序排，下一步就不用調整。")

people, roster_errors = core.parse_roster(roster_text)
for e in roster_errors:
    st.error(e)

# ---------- 3. 分析 ----------
st.subheader("3. 分析與核對")
ready = bool(files) and bool(people) and not roster_errors
if st.button("開始分析", type="primary", disabled=not ready):
    pdf_bytes = merge_pdfs(sorted(files, key=lambda f: f.name))
    with st.spinner("分析頁面中…"):
        packets, units, info = core.analyze(pdf_bytes)

    names = [p.name for p in people]
    defaults = [k + 1 if k < len(people) else 0 for k in range(len(packets))]
    details = [None] * len(packets)
    unit_ai = [None] * len(units)
    ai_note = None
    if use_ai and packets:
        with st.spinner(f"AI 辨識 {len(packets)} 個姓名中…"):
            crops = [core.render_crop(pdf_bytes, pk.name_page, scale=2.0) for pk in packets]
            assigned, details = ai_match.match(cfg, crops, names)
            if units:
                ucrops = [core.render_crop(pdf_bytes, u.id_page, box=core.PLAN_ID_BOX, scale=2.0) for u in units]
                uassigned, udetails = ai_match.match_units(cfg, ucrops, [(p.sid, p.name) for p in people])
                unit_ai = list(zip(uassigned, udetails))
        if all(d.get("error") for d in details):
            ai_note = ("error", f"AI 配對失敗，改用名單順序。錯誤：{details[0]['error']}")
            details, unit_ai = [None] * len(packets), [None] * len(units)
        else:
            defaults = [j + 1 if j is not None else 0 for j in assigned]
            n_unsure = sum(d["uncertain"] for d in details)
            ai_note = ("ok", f"AI 已自動配對，其中 {n_unsure} 套需要確認（黃色標示）。")

    st.session_state.update(
        pdf=pdf_bytes, packets=packets, units=units, info=info, defaults=defaults, details=details,
        unit_ai=unit_ai, ai_note=ai_note, zip=None,
        key=hashlib.md5(pdf_bytes + roster_text.encode()).hexdigest(),
    )

if "packets" in st.session_state and people:
    s = st.session_state
    packets, units, info, pdf_bytes = s.packets, s.units, s.info, s.pdf

    st.write(
        f"共 {info['total']} 頁，略過 {len(info['blanks'])} 張空白頁，"
        f"找到 **{len(packets)} 套**文件、**{len(units)} 組**學習指導實施計畫；名單有 **{len(people)} 位**。"
    )
    if s.get("ai_note"):
        (st.success if s.ai_note[0] == "ok" else st.error)(s.ai_note[1])
    if info.get("lead"):
        st.warning(f"第 {', '.join(str(i + 1) for i in info['lead'])} 頁在第一份「通知」之前，已略過。")
    if len(packets) != len(people):
        st.warning("文件套數和名單人數不同，請在下面確認每一套是誰。")

    labels = [f"{p.sid} {p.name}" for p in people]
    options = [SKIP] + labels
    st.caption("請對照左邊的手寫內容，確認右邊選的人正確。")

    # 3-1 每一套（依健康聲明書姓名）
    chosen = []
    for k, pk in enumerate(packets):
        c1, c2 = st.columns([3, 2])
        with c1:
            if pk.name_page is not None:
                st.image(crop(s.key, pk.name_page, (0.06, 0.155, 0.55, 0.21), pdf_bytes), width="stretch")
        with c2:
            d = s.details[k] if k < len(s.details) else None
            sel = st.selectbox(
                f"第 {k + 1} 套（第 {pk.pages[0] + 1}–{pk.pages[-1] + 1} 頁）",
                options, index=min(s.defaults[k], len(options) - 1), key=f"{s.key}-p{k}",
            )
            kinds = "、".join(KIND_LABEL[t.kind] for t in pk.type_forms) or "無"
            st.caption(f"型態同意書：{kinds}")
            if d:
                read = d["read"] or "（讀不出來）"
                (st.warning if d["uncertain"] else st.caption)(
                    f"AI 讀到「{read}」" + ("，沒把握，請確認" if d["uncertain"] else "")
                )
            for w in pk.warnings:
                st.warning(w)
        chosen.append(sel)

    # 3-2 學習指導實施計畫（依表單上的學號）
    unit_chosen = []
    if units:
        st.markdown("**研究獎助生學習指導實施計畫（依表單上的學號/姓名）**")
        for k, u in enumerate(units):
            owner = chosen[u.packet]
            ai = s.unit_ai[k] if k < len(s.unit_ai) else None
            if ai and ai[0] is not None:
                default, note = ai[0] + 1, ai[1]
            else:
                default, note = (options.index(owner) if owner in options else 0), (ai[1] if ai else None)
            c1, c2 = st.columns([3, 2])
            with c1:
                st.image(crop(s.key, u.id_page, core.PLAN_ID_BOX, pdf_bytes), width="stretch")
            with c2:
                sel = st.selectbox(
                    f"第 {k + 1} 組（第 {u.pages[0] + 1}–{u.pages[-1] + 1} 頁，夾在第 {u.packet + 1} 套）",
                    options, index=default, key=f"{s.key}-u{k}-{owner if not (ai and ai[0] is not None) else ''}",
                )
                if note:
                    how = {"sid": "學號相符", "name": "姓名相近"}.get(note.get("by"), "對不到名單")
                    msg = f"AI 讀到學號「{note['sid'] or '？'}」、姓名「{note['read'] or '？'}」（{how}）"
                    (st.warning if note["uncertain"] else st.caption)(msg + ("，請確認" if note["uncertain"] else ""))
                elif u.extra:
                    st.warning("同一套裡的第 2 組，可能是別人的，請對照學號確認")
                for w in u.warnings:
                    st.warning(w)
            unit_chosen.append(sel)

    picked = [c for c in chosen if c != SKIP]
    dupes = sorted({c for c in picked if picked.count(c) > 1})
    unused = [l for l in labels if l not in picked and l not in unit_chosen]
    if dupes:
        st.error("同一個人被選給兩套文件：" + "、".join(dupes))
    if unused:
        st.info("名單中這次沒有任何文件的人：" + "、".join(unused))

    # ---------- 4. 產生 ----------
    st.subheader("4. 產生壓縮檔")
    if st.button("分類並打包", type="primary", disabled=bool(dupes) or not (picked or any(c != SKIP for c in unit_chosen))):
        lookup = dict(zip(labels, people))
        outputs = core.plan_outputs(
            [(pk, lookup[c]) for pk, c in zip(packets, chosen) if c != SKIP],
            [(u, lookup[c]) for u, c in zip(units, unit_chosen) if c != SKIP],
        )
        with st.spinner("產生中…"):
            s.zip = core.build_zip(pdf_bytes, outputs)
        n_files = sum(len(v) for v in outputs.values())
        st.success(f"完成：{len(outputs)} 人、{n_files} 個檔案。")

    if s.get("zip"):
        st.download_button(
            "下載壓縮檔", s.zip,
            file_name=f"報到文件分類_{datetime.now():%Y%m%d_%H%M}.zip", mime="application/zip",
        )
```

### `check_llm.py`

```python
"""
檢查 .env 設定的模型伺服器能不能用（連線 + 看圖）。
用法：python check_llm.py
"""
import base64
import io
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw

import ai_match

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
cfg = ai_match.config()

if not ai_match.enabled(cfg):
    sys.exit("✗ .env 尚未填寫 LLM_BASE_URL 和 LLM_MODEL")

print(f"模型：{cfg['model']}　逾時：{cfg['timeout']:.0f} 秒　回覆上限：{cfg['max_tokens']} tokens")
try:
    models = ai_match.list_models(cfg)
except Exception as e:
    sys.exit(f"✗ 連不到伺服器：{e}\n  → 確認 LLM_BASE_URL 的 IP、埠號，以及伺服器是否允許內網連線")
if cfg["model"] not in models:
    sys.exit(f"✗ 伺服器上沒有這個模型。可用的有：{', '.join(models)}")
print("✓ 連線成功，模型存在")

img = Image.new("RGB", (240, 60), "white")
ImageDraw.Draw(img).text((10, 20), "TEST 1234", fill="black")
buf = io.BytesIO()
img.save(buf, format="PNG")
body = {
    "temperature": 0,
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Read the text in this image. Reply with the text only."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}},
    ]}],
}
print("看圖測試中（第一次要把模型載入顯示卡，可能要等幾十秒）…")
t = time.time()
try:
    text = ai_match.chat(cfg, body)
except Exception as e:
    sys.exit(f"✗ 看圖測試失敗：{e}")
print(f"模型回覆（{time.time() - t:.1f} 秒）：{text.strip()}")
print("✓ 看圖正常" if "1234" in text else "△ 有回覆但沒讀出 1234，模型可能不支援圖片")
```

### `requirements.txt`

```
streamlit>=1.46
pypdfium2>=4.30
numpy
pillow
requests
python-dotenv
```

### `.env.example`（`.env` 從這個複製，值保持空白）

```ini
# 地端視覺模型設定（Ollama / vLLM 的 OpenAI 相容 API）
# 留空 = 不使用 AI 配對，改用名單順序預設。修改後請重新啟動網頁。

# API 網址，結尾要有 /v1
#   Ollama 例：http://<模型伺服器IP>:11434/v1
#   vLLM  例：http://<模型伺服器IP>:8000/v1
LLM_BASE_URL=

# 模型名稱
#   Ollama：填 ollama ls 看到的名稱，例如 gemma4:26b
#   vLLM ：填 curl <網址>/v1/models 查到的 id
LLM_MODEL=

# API 金鑰（伺服器沒設就留空）
LLM_API_KEY=

# 單次請求逾時秒數（第一次呼叫要載入模型，建議 120）
LLM_TIMEOUT=120

# 回覆字數上限。Gemma 4 會先「思考」再回答，太小會讓答案被截掉；
# 遇到「模型沒有回覆內容」時再調高（例如 2048）
LLM_MAX_TOKENS=1024

# 進階：額外加進請求的 JSON 參數，一般留空
LLM_EXTRA_BODY=
```

### `.gitignore`

```
.env
__pycache__/
```

### `.streamlit/config.toml`

```toml
[server]
# 只允許本機連線，掃描檔不會被區網其他電腦存取
address = "localhost"
port = 8501
maxUploadSize = 1000

[browser]
gatherUsageStats = false

[client]
toolbarMode = "minimal"
```

### `啟動.bat`（Windows，請存成 CRLF 換行）

```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
echo 第一次執行會安裝套件，請稍候...
python -m pip install -q -r requirements.txt
python -m streamlit run app.py
pause
```

### `啟動.command`（Mac / Linux，建立後 `chmod +x 啟動.command`）

```bash
#!/bin/bash
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
echo "第一次執行會安裝套件，請稍候..."
python3 -m pip install -q -r requirements.txt
python3 -m streamlit run app.py
```

### `使用說明.txt`

請用繁體中文、口語化地寫給非工程背景的使用者，內容包含：

- **第一次安裝：** 需 Python 3.10+（Windows 安裝時勾選「Add python.exe to PATH」），雙擊啟動檔。
- **設定模型：** 打開 `.env`，填 `LLM_BASE_URL`（結尾要有 `/v1`）和 `LLM_MODEL`，存檔後重新啟動。執行 `python check_llm.py` 測試，或按網頁側邊欄「測試連線」。
  - Ollama 的模型名稱用 `ollama ls` 查（例如 `gemma4:26b`）；vLLM 用 `curl <網址>/v1/models` 查 `id`。
  - 出現「模型沒有回覆內容…請調高 LLM_MAX_TOKENS」時，把 `.env` 的 `LLM_MAX_TOKENS` 改成 2048 再試。
  - 第一次分析比較慢（模型要載入顯示卡），逾時的話再按一次。
- **每次使用：** 丟掃描檔 → 貼名單（順序不限；補交實施計畫的人也要放進名單）→ 開始分析 → 看一眼，黃色的要確認 → 分類並打包 → 下載。
- **壓縮檔內容**（同第 4 節），特別說明「國科會計畫-」開頭的檔案代表研究獎助生。
- **隱私：** 掃描檔只在本機處理，只有姓名欄、學號欄小圖會送到 `.env` 設定的地端模型伺服器，不含身分證字號。
- **沒設定模型也能用：** 這時依名單順序預設配對，名單照掃描順序貼就好；夾在別人後面的實施計畫要手動選人。

## 12. 驗收

`.env` 的值由使用者填，所以此階段**不要**測試真正的模型連線。請依序確認：

1. 建立虛擬環境（建議）並 `pip install -r requirements.txt` 成功。
2. `python -m py_compile app.py core.py ai_match.py check_llm.py` 沒有錯誤。
3. `.env` 已從 `.env.example` 複製，**值都是空的**。
4. `python check_llm.py` 應顯示「.env 尚未填寫 LLM_BASE_URL 和 LLM_MODEL」（若是從舊版升級、`.env` 已有值，則跳過此項，不要測試連線、不要印出 `.env`）。
5. `python -m streamlit run app.py` 能啟動，瀏覽器開 `http://localhost:8501`：標題「報到文件分類」、側邊欄顯示「尚未設定」、終端機沒有 `use_container_width` 棄用警告。
6. 若使用者手邊有掃描檔，可用 `core.analyze()` 測試，只印頁碼、套數、型態種類和警示，**不要印出姓名欄或身分證字號**。預期：
   - 空白頁全部略過，套數＝人數。
   - 每張型態同意書都判斷出「研究獎助生」或「兼任研究助理」，沒有「判斷不出」。
   - 學習指導實施計畫每組 3 頁；夾在別人後面的那組被標為第 2 組（extra）。
   - 輸出的頁面方向和原始掃描相同（沒有被轉正）。
7. 回報給使用者：怎麼啟動、`.env` 在哪裡、要填哪兩個值（以及可選的 `LLM_MAX_TOKENS`），填好後執行 `python check_llm.py` 測試。

## 13. 已知限制

- 前 5 頁順序固定、第一張非空白頁必須是通知頁。表單版本或順序改了，要調整 `Packet` 索引與裁切座標。
- 型態判斷依賴「指導教授在勾選欄蓋紅色印章」。沒蓋章、用黑色章或印章蓋到中間時會顯示「判斷不出」，需人工確認（檔案先歸為一般「型態」）。
- 實施計畫的參考頁是文件中第一張直式後段頁；若某份掃描檔的後段第一張不是實施計畫第 1 頁，分組可能出錯（會有頁數不是 3 的提醒）。
- AI 配對準確度取決於模型和字跡；`CONFIDENT = 0.7`、`MIN_SCORE = 0.6` 是在模擬環境下設定的，可依實際情況調整。
- 「會先思考」的模型若思考過長，答案會被截掉；調高 `LLM_MAX_TOKENS` 即可，代價是每張圖稍慢。