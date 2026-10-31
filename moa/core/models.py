"""v2 のエンティティ（3〜4章）：Character / Outfit / Background / Action / Scene。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    message: str
    path: Path | None = None
    field: str | None = None  # 項目のパス（例 mossItems[2].bbox、outfits.left）。エディタの一覧表示用

    def __str__(self) -> str:
        where = f" [{self.path.name}]" if self.path else ""
        return f"{self.level}: {self.message}{where}"


def has_error(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


@dataclass
class Character:
    id: str
    name: str
    moss: str
    anima: str
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    sex: str = "female"  # female | male。人数タグの集計に使う（7.4章）
    default_outfit: str | None = None
    body_hair_moss: str = ""   # 量＋色を含む完全な文（服装の moss と同じ）。{{slot.bodyHair}} の差し込み先
    body_hair_anima: str = ""
    negative_moss: str = ""
    negative_anima: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        return cls(
            id=d["id"], name=d["name"], moss=d["moss"], anima=d["anima"],
            group=d.get("group"), tags=list(d.get("tags") or []),
            sex=d.get("sex") or "female",
            default_outfit=d.get("defaultOutfit"),
            body_hair_moss=d.get("bodyHairMoss") or "",
            body_hair_anima=d.get("bodyHairAnima") or "",
            negative_moss=d.get("negativeMoss") or "",
            negative_anima=d.get("negativeAnima") or "",
        )


@dataclass
class Outfit:
    id: str
    name: str
    moss: str
    anima: str
    tags: list[str] = field(default_factory=list)
    negative_moss: str = ""
    negative_anima: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Outfit":
        return cls(
            id=d["id"], name=d["name"], moss=d["moss"], anima=d["anima"],
            tags=list(d.get("tags") or []),
            negative_moss=d.get("negativeMoss") or "",
            negative_anima=d.get("negativeAnima") or "",
        )


@dataclass
class MossItem:
    label: str
    caption: str
    bbox: list[int]  # [y_min, x_min, y_max, x_max]

    @classmethod
    def from_dict(cls, d: dict) -> "MossItem":
        return cls(label=d["label"], caption=d["caption"], bbox=list(d["bbox"]))


@dataclass
class Background:
    id: str
    name: str
    canvas: dict
    moss_caption: str
    anima: str
    tags: list[str] = field(default_factory=list)
    rating: str = "safe"
    meta_tags: list[str] = field(default_factory=list)
    moss_items: list[MossItem] = field(default_factory=list)
    negative_moss: str = ""
    negative_anima: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Background":
        return cls(
            id=d["id"], name=d["name"], canvas=dict(d["canvas"]),
            moss_caption=d["mossCaption"], anima=d["anima"],
            tags=list(d.get("tags") or []), rating=d.get("rating") or "safe",
            meta_tags=list(d.get("metaTags") or []),
            moss_items=[MossItem.from_dict(i) for i in d.get("mossItems") or []],
            negative_moss=d.get("negativeMoss") or "",
            negative_anima=d.get("negativeAnima") or "",
        )


@dataclass
class Action:
    id: str
    name: str
    slots: list[str]
    moss_caption: str
    moss_items: list[MossItem]
    anima: str
    tags: list[str] = field(default_factory=list)
    rating: str = "safe"
    background: str | None = None  # 推奨背景。Scene 作成時の初期値にだけ使う
    outfits: dict[str, str] = field(default_factory=dict)
    extras: dict[str, int] = field(default_factory=dict)  # female / male / others
    meta_tags: list[str] = field(default_factory=list)
    negative_moss: str = ""
    negative_anima: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        return cls(
            id=d["id"], name=d["name"], slots=list(d["slots"]),
            moss_caption=d["mossCaption"],
            moss_items=[MossItem.from_dict(i) for i in d["mossItems"]],
            anima=d["anima"],
            tags=list(d.get("tags") or []), rating=d.get("rating") or "safe",
            background=d.get("background"),
            outfits=dict(d.get("outfits") or {}), extras=dict(d.get("extras") or {}),
            meta_tags=list(d.get("metaTags") or []),
            negative_moss=d.get("negativeMoss") or "",
            negative_anima=d.get("negativeAnima") or "",
        )


@dataclass
class CastEntry:
    character: str
    outfit: str | None = None  # None＝省略（委譲） / "none" / Outfit の id


@dataclass
class Scene:
    id: str
    background: str
    action: str
    cast: dict[str, CastEntry]
    name: str | None = None
    apply_negative: bool = False
    gen: dict = field(default_factory=dict)  # 書いた項目だけワークフローの値を上書きする（13.5章）

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        cast = {slot: CastEntry(e["character"], e.get("outfit")) for slot, e in d["cast"].items()}
        gen = {stage: dict(params or {}) for stage, params in (d.get("gen") or {}).items()}
        return cls(
            id=d["id"], background=d["background"], action=d["action"], cast=cast,
            name=d.get("name"), apply_negative=bool(d.get("applyNegative", False)), gen=gen,
        )


@dataclass
class FolderMeta:
    name: str | None = None
    description: str | None = None
    order: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "FolderMeta":
        return cls(name=d.get("name"), description=d.get("description"),
                   order=list(d.get("order") or []))


@dataclass(frozen=True)
class SlotResolution:
    slot: str
    character_id: str
    outfit_id: str | None
    outfit_source: str  # scene | action | character | none_scene | none_action | none_character | unset


@dataclass
class CompileResult:
    scene_id: str
    background_id: str
    action_id: str
    apply_negative: bool = False
    canvas: dict | None = None
    rating: str = "safe"          # 実効 rating（厳しい方）
    people_tags: str = ""         # 例 "1girl, solo"
    moss_prompt: str = ""
    anima_prompt: str = ""
    negative_moss: str = ""       # 合成済み（適用OFFでも常に入る）
    negative_anima: str = ""
    slots: list[SlotResolution] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_error(self.issues)

    @property
    def negative_moss_payload(self) -> str:
        return self.negative_moss if self.apply_negative else ""

    @property
    def negative_anima_payload(self) -> str:
        return self.negative_anima if self.apply_negative else ""
