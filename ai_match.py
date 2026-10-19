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
