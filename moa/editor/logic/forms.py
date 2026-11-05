"""エンティティ ↔ フォーム値の変換（EDITOR_SPEC 4章・10章）。Gradio に依存しない。

フォーム値（values）は YAML のキー名のままの dict。画面の部品との対応づけは ui 側で行う。
"""
from __future__ import annotations

from moa.core.constants import NONE
from moa.core.entity_io import ORDER, REQUIRED
from moa.core.models import Action, Background, Character, Outfit

KINDS = ("character", "outfit", "background", "action")
KIND_LABEL = {"character": "キャラ", "outfit": "服装", "background": "背景", "action": "行為"}
ROOT_OF = {"character": "characters", "outfit": "outfits", "background": "backgrounds", "action": "actions"}

LIST_KEYS = ("tags", "metaTags", "slots")
TEXT_KEYS = ("name", "group", "moss", "anima", "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima",
             "mossCaption")
DEFAULT_CANVAS = {"w": 1536, "h": 1024}
MAX_ITEMS = 64          # mossItems の実際の上限（カードも図もここまで追加できる。カードは最初にこの数だけ用意する）
RECOMMENDED_ITEMS = 8   # 画面の文言でおすすめする件数。超えても、警告やエラーにはしない


# ---- カンマ区切りの一覧 ----
def split_list(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def join_list(items) -> str:
    return ", ".join(items or [])


# ---- 服装指定の三値 ----
def outfit_mode(value: str | None) -> tuple[str, str | None]:
    """('unset', None) / ('none', None) / ('outfit', id)。"""
    if value is None:
        return "unset", None
    if value == NONE:
        return "none", None
    return "outfit", value


def outfit_value(mode: str, outfit_id: str | None) -> str | None:
    """三値 → 書く値。未指定は None（キーを書かない）。`null` は書かない。"""
    if mode == "none":
        return NONE
    if mode == "outfit" and outfit_id:
        return outfit_id
    return None


# ---- 初期値 ----
def full_bbox(canvas: dict) -> list[int]:
    """キャンバス全体 `[-H/2, -W/2, H/2, W/2]`。"""
    w, h = int(canvas["w"]), int(canvas["h"])
    return [-(h // 2), -(w // 2), h // 2, w // 2]


def new_item(canvas: dict) -> dict:
    return {"label": "object", "caption": "", "bbox": full_bbox(canvas)}


def new_values(kind: str) -> dict:
    """10章の新規作成時の初期値。"""
    base = {"id": "", "name": "", "tags": []}
    if kind == "character":
        return base | {"group": "", "sex": "female", "defaultOutfit": None, "moss": "", "anima": "",
                       "bodyHairMoss": "", "bodyHairAnima": "", "negativeMoss": "", "negativeAnima": ""}
    if kind == "outfit":
        return base | {"moss": "", "anima": "", "negativeMoss": "", "negativeAnima": ""}
    if kind == "background":
        return base | {"rating": "safe", "canvas": dict(DEFAULT_CANVAS), "metaTags": [], "mossCaption": "",
                       "mossItems": [], "anima": "", "negativeMoss": "", "negativeAnima": ""}
    if kind == "action":
        return base | {"rating": "safe", "background": None, "slots": ["main"], "outfits": {}, "extras": {},
                       "metaTags": [], "mossCaption": "", "mossItems": [new_item(DEFAULT_CANVAS)],
                       "anima": "", "negativeMoss": "", "negativeAnima": ""}
    raise ValueError(kind)


def values_of(obj) -> dict:
    """保存済みのエンティティ → フォーム値。"""
    if isinstance(obj, Character):
        return {"id": obj.id, "name": obj.name, "group": obj.group or "", "tags": list(obj.tags), "sex": obj.sex,
                "defaultOutfit": obj.default_outfit, "moss": obj.moss, "anima": obj.anima,
                "bodyHairMoss": obj.body_hair_moss, "bodyHairAnima": obj.body_hair_anima,
                "negativeMoss": obj.negative_moss, "negativeAnima": obj.negative_anima}
    if isinstance(obj, Outfit):
        return {"id": obj.id, "name": obj.name, "tags": list(obj.tags), "moss": obj.moss, "anima": obj.anima,
                "negativeMoss": obj.negative_moss, "negativeAnima": obj.negative_anima}
    items = lambda o: [{"label": i.label, "caption": i.caption, "bbox": list(i.bbox)} for i in o.moss_items]  # noqa: E731
    if isinstance(obj, Background):
        return {"id": obj.id, "name": obj.name, "tags": list(obj.tags), "rating": obj.rating,
                "canvas": dict(obj.canvas), "metaTags": list(obj.meta_tags), "mossCaption": obj.moss_caption,
                "mossItems": items(obj), "anima": obj.anima,
                "negativeMoss": obj.negative_moss, "negativeAnima": obj.negative_anima}
    if isinstance(obj, Action):
        return {"id": obj.id, "name": obj.name, "tags": list(obj.tags), "rating": obj.rating,
                "background": obj.background, "slots": list(obj.slots), "outfits": dict(obj.outfits),
                "extras": dict(obj.extras), "metaTags": list(obj.meta_tags), "mossCaption": obj.moss_caption,
                "mossItems": items(obj), "anima": obj.anima,
                "negativeMoss": obj.negative_moss, "negativeAnima": obj.negative_anima}
    raise TypeError(type(obj))


def copy_values(values: dict) -> dict:
    """複製して新規作成：内容はそのまま、新 ID の初期値は `<元のid>_copy`。"""
    out = normalize(values_kind(values), values)
    out["id"] = f"{values['id']}_copy"
    return out


def values_kind(values: dict) -> str:
    if "slots" in values:
        return "action"
    if "canvas" in values:
        return "background"
    if "sex" in values:
        return "character"
    return "outfit"


# ---- 正規化 ----
def normalize(kind: str, values: dict) -> dict:
    """比較・検証・保存用に整える。文字列は前後の空白を除き、一覧は空要素を捨て、extras の 0 と未指定の服装は落とす。"""
    out: dict = {}
    for key in ORDER[kind]:
        if key not in values:
            continue
        v = values[key]
        if key in LIST_KEYS:
            seq = v if isinstance(v, (list, tuple)) else split_list(v)
            out[key] = [s.strip() for s in seq if s and s.strip()]
        elif key == "extras":
            out[key] = {k: int(n) for k, n in (v or {}).items() if int(n or 0) > 0}
        elif key == "outfits":
            out[key] = {s: o for s, o in (v or {}).items() if o}
        elif key == "canvas":
            out[key] = {"w": int(v["w"]), "h": int(v["h"])}
        elif key == "mossItems":
            out[key] = [{"label": (i.get("label") or "").strip(), "caption": (i.get("caption") or "").strip(),
                         "bbox": [int(n) for n in i["bbox"]]} for i in (v or [])]
        elif key in ("defaultOutfit", "background"):
            out[key] = v or None
        elif isinstance(v, str):
            out[key] = v.strip()
        else:
            out[key] = v
    return out


def is_dirty(kind: str, current: dict, loaded: dict) -> bool:
    return normalize(kind, current) != normalize(kind, loaded)


def to_yaml_dict(kind: str, values: dict) -> dict:
    """スキーマ検証にかける dict（保存されるファイルと同じ見え方：空の任意項目は無い）。"""
    v = normalize(kind, values)
    out = {}
    for key, val in v.items():
        if key not in REQUIRED[kind] and not val:
            continue
        out[key] = val
    return out


def to_obj(kind: str, values: dict):
    """フォーム値 → models のオブジェクト（プレビュー・意味チェック用）。"""
    d = to_yaml_dict(kind, values)
    d.setdefault("mossItems", [])
    cls = {"character": Character, "outfit": Outfit, "background": Background, "action": Action}[kind]
    return cls.from_dict(d)
