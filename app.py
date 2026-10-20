"""
SortOCR｜PDF 分類工坊 網頁

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

st.set_page_config(page_title="SortOCR｜PDF 分類工坊", page_icon="🛠️", layout="wide")
st.html('<style>[data-testid="stMainBlockContainer"] { max-width: 1400px; }</style>')
st.title("SortOCR｜PDF 分類工坊")
st.caption("把報到時繳交的整疊掃描檔，依人切分、命名並打包。掃描檔只在這台電腦處理。")

KIND_LABEL = {core.GRANT: "研究獎助生", core.ASSISTANT: "兼任研究助理", core.UNKNOWN: "判斷不出"}
SKIP = "（略過）"


ACCENTS = {
    "blue": ("🔵 藍", None),
    "green": ("🟢 綠", ("#2E9E6B", "#3DBE85")),
    "violet": ("🟣 紫", ("#7C5CDB", "#9B82EE")),
    "orange": ("🟠 橘", ("#D9702E", "#F0914F")),
}


ACCENT_CSS = """
button[kind="primary"]:not(:disabled) {
  background-color: var(--accent) !important; border-color: var(--accent) !important;
}
button[kind="primary"]:not(:disabled):is(:hover, :focus-visible) {
  background-color: color-mix(in srgb, var(--accent) 85%, black) !important;
  border-color: color-mix(in srgb, var(--accent) 85%, black) !important;
}
button:is([kind="pillsActive"], [kind="segmented_controlActive"]):not(:disabled) {
  background-color: color-mix(in srgb, var(--accent) 12%, transparent) !important;
  border-color: var(--accent) !important; color: var(--accent) !important;
}
[data-testid="stTab"]:is([data-selected], [data-hovered], [data-focus-visible]) { color: var(--accent) !important; }
[data-testid="stTab"][data-selected] .react-aria-SelectionIndicator { background-color: var(--accent) !important; }
[data-testid="stCheckbox"] [data-selected]:not([data-disabled]) .e1ap2qgk7 { background-color: var(--accent) !important; }
[data-testid="stTextAreaRootElement"]:focus-within { border-color: var(--accent) !important; }
"""


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


def step_title(n: int, title: str):
    st.markdown(f"##### {'①②③④'[n - 1]}　{title}")


def step(n: int, title: str):
    """每個步驟一張有框的卡片。"""
    box = st.container(border=True)
    with box:
        step_title(n, title)
    return box


def grid(items, n: int = 2):
    """把卡片排成每列 n 張（手機上自動變一欄），逐一回傳 (欄位, 項目)。"""
    for i in range(0, len(items), n):
        for col, item in zip(st.columns(n), items[i:i + n]):
            yield col, item


def card_title(text: str, pages: list, d: dict | None):
    """核對卡片的標題列：第幾套、頁碼、AI 狀態標籤。"""
    tag = ""
    if d:
        tag = "　:orange-badge[需確認]" if d["uncertain"] else "　:green-badge[AI 已配對]"
    span = f"{pages[0] + 1}" if pages[0] == pages[-1] else f"{pages[0] + 1}–{pages[-1] + 1}"
    st.markdown(f"**{text}**　:gray[第 {span} 頁]{tag}")


# ---------- 側邊欄：AI 配對設定 ----------
with st.sidebar:
    st.header("AI 自動配對")
    if ai_match.enabled(cfg):
        st.badge("已設定", icon=":material/check_circle:", color="green")
        st.caption(f"模型：`{cfg['model']}`")
        use_ai = st.toggle("使用 AI 配對", value=True)
        if st.button("測試連線", icon=":material/network_check:", width="stretch"):
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
        st.badge("尚未設定", icon=":material/info:", color="gray")
        st.caption("請在 .env 填入 LLM_BASE_URL 和 LLM_MODEL 後重新啟動。未設定時會依名單順序預設配對。")
    st.divider()
    st.header("外觀")
    # 選擇記在網址（?accent=green），重新整理或加書籤都會保留
    saved = st.query_params.get("accent", "blue")
    accent = st.pills(
        "強調色", list(ACCENTS), default=saved if saved in ACCENTS else "blue", required=True,
        format_func=lambda k: ACCENTS[k][0], key="accent",
    )
    if accent == "blue":
        st.query_params.pop("accent", None)
    else:
        st.query_params["accent"] = accent
        light, dark = ACCENTS[accent][1]
        color = dark if st.context.theme.type == "dark" else light
        st.html(f"<style>:root {{ --accent: {color}; }}{ACCENT_CSS}</style>")
    st.caption("淺色 / 深色：點右上角 ⋮，選 Light、Dark 或 System（跟著電腦設定）。每台電腦會各自記住。")
    st.divider()
    st.caption("啟用 AI 時，只有姓名欄 / 學號欄的小圖會送到 .env 設定的地端模型伺服器。")

tab_sort, tab_merge = st.tabs([":material/folder_open: PDF 分類", ":material/picture_as_pdf: 合併 PDF"])

# ==================== PDF 分類 ====================
with tab_sort:
    # 1、2 左右並排（有框的欄位會等高），手機上自動上下疊
    col1, col2 = st.columns(2, border=True)

    # ---------- 1. 掃描檔 ----------
    with col1:
        step_title(1, "上傳掃描檔")
        files = st.file_uploader("可一次丟多個 PDF，會依檔名順序處理", type="pdf", accept_multiple_files=True)
        if files:
            mb = sum(f.size for f in files) / 1024 / 1024
            st.caption(f":material/check: 已選 {len(files)} 個檔案，共 {mb:.1f} MB。")
        else:
            st.caption("整疊雙面掃描成 PDF 即可；分成多個檔時，會依檔名排序後接在一起。")

    # ---------- 2. 名單 ----------
    with col2:
        step_title(2, "貼上名單")
        roster_text = st.text_area(
            "每行一位：學號 姓名（可直接從 Excel 複製「組別 學號 姓名」貼上）",
            height=200,
            placeholder="AIoT\t1411230001\t王小明\nMobile\t1411230002\t陳小華\n1811530001 林小美",
        )
        people, roster_errors = core.parse_roster(roster_text)
        for e in roster_errors:
            st.error(e)
        if people and not roster_errors:
            st.caption(f":material/check: 已讀到 {len(people)} 位。有組別時會多一層組別資料夾。")
        else:
            st.caption("有組別時會多一層組別資料夾。沒有啟用 AI 配對時，名單照掃描順序排，下一步就不用調整。")

    # ---------- 3. 分析 ----------
    with step(3, "分析與核對"):
        ready = bool(files) and bool(people) and not roster_errors
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        with c1:
            ok, todo = ":green[:material/check_circle:]", ":gray[:material/radio_button_unchecked:]"
            st.markdown(
                f"{ok if files else todo} 掃描檔　　{ok if people and not roster_errors else todo} 名單"
                + ("" if ready else "　　:gray[兩項都完成才能開始分析]")
            )
        with c2:
            clicked = st.button("開始分析", type="primary", icon=":material/search:", disabled=not ready, width="stretch")

    if clicked:
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
                ai_note = ("ok", f"AI 已自動配對，其中 {n_unsure} 套需要確認（標示「需確認」）。")

        st.session_state.update(
            pdf=pdf_bytes, packets=packets, units=units, info=info, defaults=defaults, details=details,
            unit_ai=unit_ai, ai_note=ai_note, zip=None,
            key=hashlib.md5(pdf_bytes + roster_text.encode()).hexdigest(),
        )

    if "packets" in st.session_state and people:
        s = st.session_state
        packets, units, info, pdf_bytes = s.packets, s.units, s.info, s.pdf

        # 外層兩欄在手機上會上下疊，內層不疊 → 電腦 4 個一排、手機 2×2
        metrics = [
            ("總頁數", info["total"], f"已略過 {len(info['blanks'])} 張空白頁"),
            ("文件套數", len(packets), None),
            ("實施計畫", len(units), None),
            ("名單人數", len(people), None),
        ]
        for half, pair in zip(st.columns(2), (metrics[:2], metrics[2:])):
            for col, (label, value, tip) in zip(half.columns(2, wrap=False), pair):
                col.metric(label, value, help=tip, border=True)

        if s.get("ai_note"):
            (st.success if s.ai_note[0] == "ok" else st.error)(s.ai_note[1])
        if info.get("lead"):
            st.warning(f"第 {', '.join(str(i + 1) for i in info['lead'])} 頁在第一份「通知」之前，已略過。")
        if len(packets) != len(people):
            st.warning("文件套數和名單人數不同，請在下面確認每一套是誰。")

        labels = [f"{p.sid} {p.name}" for p in people]
        options = [SKIP] + labels

        # 3-1 每一套（依健康聲明書姓名）
        st.markdown("#### 核對每一套文件")
        st.caption("對照左邊的手寫姓名，確認右邊選的人正確。")
        chosen = []
        for col, (k, pk) in grid(list(enumerate(packets))):
            d = s.details[k] if k < len(s.details) else None
            with col.container(border=True):
                card_title(f"第 {k + 1} 套", pk.pages, d)
                if pk.name_page is not None:
                    st.image(crop(s.key, pk.name_page, (0.06, 0.155, 0.55, 0.21), pdf_bytes), width="stretch")
                else:
                    st.caption("找不到姓名頁")
                sel = st.selectbox(
                    f"第 {k + 1} 套是誰", options, index=min(s.defaults[k], len(options) - 1),
                    key=f"{s.key}-p{k}", label_visibility="collapsed",
                )
                kinds = "、".join(KIND_LABEL[t.kind] for t in pk.type_forms) or "無"
                note = f"型態同意書：{kinds}"
                if d:
                    note += f"　·　AI 讀到「{d['read'] or '（讀不出來）'}」" + ("，沒把握，請確認" if d["uncertain"] else "")
                st.caption(note)
                for w in pk.warnings:
                    st.warning(w)
            chosen.append(sel)

        # 3-2 學習指導實施計畫（依表單上的學號）
        unit_chosen = []
        if units:
            st.markdown("#### 核對學習指導實施計畫")
            st.caption("研究獎助生的實施計畫依表單上的學號 / 姓名歸檔。")
            for col, (k, u) in grid(list(enumerate(units))):
                owner = chosen[u.packet]
                ai = s.unit_ai[k] if k < len(s.unit_ai) else None
                if ai and ai[0] is not None:
                    default, note = ai[0] + 1, ai[1]
                else:
                    default, note = (options.index(owner) if owner in options else 0), (ai[1] if ai else None)
                with col.container(border=True):
                    card_title(f"第 {k + 1} 組", u.pages, note)
                    st.image(crop(s.key, u.id_page, core.PLAN_ID_BOX, pdf_bytes), width="stretch")
                    sel = st.selectbox(
                        f"第 {k + 1} 組是誰", options, index=default, label_visibility="collapsed",
                        key=f"{s.key}-u{k}-{owner if not (ai and ai[0] is not None) else ''}",
                    )
                    caption = f"夾在第 {u.packet + 1} 套裡"
                    if note:
                        how = {"sid": "學號相符", "name": "姓名相近"}.get(note.get("by"), "對不到名單")
                        caption += (
                            f"　·　AI 讀到學號「{note['sid'] or '？'}」、姓名「{note['read'] or '？'}」（{how}）"
                            + ("，請確認" if note["uncertain"] else "")
                        )
                    st.caption(caption)
                    if not note and u.extra:
                        st.warning("同一套裡的第 2 組，可能是別人的，請對照學號確認")
                    for w in u.warnings:
                        st.warning(w)
                unit_chosen.append(sel)

        picked = [c for c in chosen if c != SKIP]
        dupes = sorted({c for c in picked if picked.count(c) > 1})
        unused = [l for l in labels if l not in picked and l not in unit_chosen]

        # ---------- 4. 產生 ----------
        with step(4, "產生壓縮檔"):
            if dupes:
                st.error("同一個人被選給兩套文件：" + "、".join(dupes))
            if unused:
                st.info("名單中這次沒有任何文件的人：" + "、".join(unused))
            c1, c2 = st.columns(2)
            with c1:
                pack = st.button(
                    "分類並打包", type="primary", icon=":material/inventory_2:", width="stretch",
                    disabled=bool(dupes) or not (picked or any(c != SKIP for c in unit_chosen)),
                )
            if pack:
                lookup = dict(zip(labels, people))
                outputs = core.plan_outputs(
                    [(pk, lookup[c]) for pk, c in zip(packets, chosen) if c != SKIP],
                    [(u, lookup[c]) for u, c in zip(units, unit_chosen) if c != SKIP],
                )
                with st.spinner("產生中…"):
                    s.zip = core.build_zip(pdf_bytes, outputs)
                s.zip_note = f"完成：{len(outputs)} 人、{sum(len(v) for v in outputs.values())} 個檔案。"
            with c2:
                st.download_button(
                    "下載壓縮檔", s.get("zip") or b"", icon=":material/download:", width="stretch",
                    file_name=f"SortOCR_{datetime.now():%Y%m%d_%H%M}.zip", mime="application/zip",
                    disabled=not s.get("zip"),
                )
            if s.get("zip"):
                st.success(s.get("zip_note", "壓縮檔已產生。"))


# ==================== 合併 PDF ====================
with tab_merge:
    st.caption("把多個 PDF 接成一份（頁面維持原始方向）。只在這台電腦處理，不會送給 AI 模型。")
    col1, col2 = st.columns(2, border=True)
    with col1:
        step_title(1, "選擇檔案")
        merge_files = st.file_uploader(
            "選擇要合併的 PDF（可多選）", type="pdf", accept_multiple_files=True, key="merge_files"
        )

    with col2:
        step_title(2, "確認順序")
        if not merge_files:
            st.caption("選好檔案後，這裡會列出合併順序和每個檔案的頁數。")
        else:
            order = st.segmented_control(
                "合併順序", ["依檔名", "依加入順序"], default="依檔名", required=True, key="merge_order"
            )
            ordered = sorted(merge_files, key=lambda f: f.name) if order == "依檔名" else list(merge_files)

            total, bad, rows = 0, [], []
            for i, f in enumerate(ordered, 1):
                try:
                    n = len(pdfium.PdfDocument(f.getvalue()))
                except pdfium.PdfiumError:
                    bad.append(f.name)
                    rows.append({"順序": i, "檔名": f.name, "頁數": "❌ 無法讀取"})
                    continue
                total += n
                rows.append({"順序": i, "檔名": f.name, "頁數": str(n)})
            st.dataframe(rows, hide_index=True, width="stretch")
            if bad:
                st.error("這些檔案無法讀取，請移除後再合併：" + "、".join(bad))
            st.markdown(f"合併後共 **{total} 頁**")

    if merge_files:
        with step(3, "合併與下載"):
            sig = hashlib.md5("|".join(f"{f.name}:{f.size}" for f in ordered).encode()).hexdigest()
            c1, c2 = st.columns(2)
            with c1:
                if st.button(
                    "合併", type="primary", icon=":material/merge:", width="stretch",
                    disabled=bool(bad) or len(ordered) < 2, key="merge_btn",
                ):
                    with st.spinner("合併中…"):
                        st.session_state.merged = (sig, merge_pdfs(ordered))
            merged = st.session_state.get("merged")
            done = bool(merged and merged[0] == sig)
            with c2:
                st.download_button(
                    "下載合併後的 PDF", merged[1] if done else b"", icon=":material/download:", width="stretch",
                    file_name=f"合併_{datetime.now():%Y%m%d_%H%M}.pdf", mime="application/pdf",
                    key="merge_dl", disabled=not done,
                )
            if len(ordered) < 2:
                st.caption("至少要選 2 個檔案才能合併。")
            elif done:
                st.success("合併完成！")
