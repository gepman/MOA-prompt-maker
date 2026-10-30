import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "spec" / "schemas"

NONE = "none"

ID_RE = re.compile(r"^[a-z0-9_]+$")

# {{slot}} / {{slot.outfit}} の2種のみ（5章）
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)(?:\.(outfit))?\s*\}\}")
ANY_BRACES_RE = re.compile(r"\{\{(.*?)\}\}", re.S)

# 全体ベースのネガティブ（6章。アプリ側の定数）
BASE_NEGATIVE_MOSS = (
    "photorealistic, realistic, blurry, low quality, worst quality, text, watermark, "
    "deformed hands, extra fingers, extra limbs, censored, mosaic"
)
BASE_NEGATIVE_ANIMA = (
    "worst quality, low quality, score_1, score_2, score_3, artist name, blurry, "
    "jpeg artifacts, chromatic aberration, censored, mosaic censoring"
)
