import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "spec_v2" / "schemas"

NONE = "none"

# シードの値域は 0〜2^32−1（13.2章）
SEED_MAX = 2**32 - 1

ID_RE = re.compile(r"^[a-z0-9_]+$")

# {{slot}} / {{slot.outfit}} / {{slot.bodyHair}} の3種のみ（5.1章）。接尾辞は大文字小文字を区別する
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)(?:\.(outfit|bodyHair))?\s*\}\}")
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

# コンパイラが組み立てる固定値（7章。ファイルには書かない）
QUALITY_TAGS = "masterpiece, best quality, score_7"
RATINGS = ("safe", "sensitive", "explicit")  # 厳しい順に右。safe < sensitive < explicit
META_BASE = "anime illustration"
MOSS_CANVAS = "single"
MOSS_COORD_SPACE = "abs_centralized"
MOSS_BBOX_FORMAT = "yxyx"

# テンプレート側に書かれた人数タグ（7.4章）。`solo` は単独の要素として別に判定する
COUNT_TAG_RE = re.compile(r"^(\d+\+?)(girl|girls|boy|boys)$", re.IGNORECASE)

# ルート名 → エンティティの種類（8章）
ROOTS = {
    "characters": "character",
    "outfits": "outfit",
    "backgrounds": "background",
    "actions": "action",
    "scenes": "scene",
}
