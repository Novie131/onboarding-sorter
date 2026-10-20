"""
SortOCR（PDF 分類工坊）：核心邏輯（不含網頁介面）

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
