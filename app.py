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

tab_sort, tab_merge = st.tabs(["報到文件分類", "合併 PDF"])

# ==================== 報到文件分類 ====================
with tab_sort:
    st.caption("丟入掃描檔 → 貼上名單 → 分析 → 下載壓縮檔。")

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


# ==================== 合併 PDF ====================
with tab_merge:
    st.caption("把多個 PDF 接成一份（頁面維持原始方向）。只在這台電腦處理，不會送給 AI 模型。")
    merge_files = st.file_uploader(
        "選擇要合併的 PDF（可多選）", type="pdf", accept_multiple_files=True, key="merge_files"
    )
    if merge_files:
        order = st.radio("合併順序", ["依檔名", "依加入順序"], horizontal=True, key="merge_order")
        ordered = sorted(merge_files, key=lambda f: f.name) if order == "依檔名" else list(merge_files)

        total, bad = 0, []
        for i, f in enumerate(ordered, 1):
            try:
                n = len(pdfium.PdfDocument(f.getvalue()))
            except pdfium.PdfiumError:
                bad.append(f.name)
                st.write(f"{i}. {f.name}　❌ 無法讀取")
                continue
            total += n
            st.write(f"{i}. {f.name}　（{n} 頁）")
        if bad:
            st.error("這些檔案無法讀取，請移除後再合併：" + "、".join(bad))
        st.write(f"合併後共 **{total} 頁**。")

        sig = hashlib.md5("|".join(f"{f.name}:{f.size}" for f in ordered).encode()).hexdigest()
        if st.button("合併", type="primary", disabled=bool(bad) or len(ordered) < 2, key="merge_btn"):
            with st.spinner("合併中…"):
                st.session_state.merged = (sig, merge_pdfs(ordered))
        if len(ordered) < 2:
            st.caption("至少要選 2 個檔案才能合併。")

        merged = st.session_state.get("merged")
        if merged and merged[0] == sig:
            st.success("合併完成！")
            st.download_button(
                "下載合併後的 PDF",
                merged[1],
                file_name=f"合併_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf",
                key="merge_dl",
            )
