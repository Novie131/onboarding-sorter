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
