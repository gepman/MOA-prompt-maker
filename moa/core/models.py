from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    message: str
    path: Path | None = None

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
    default_outfit: str | None = None
    negative_moss: str = ""
    negative_anima: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        return cls(
            id=d["id"], name=d["name"], moss=d["moss"], anima=d["anima"],
            group=d.get("group"), tags=list(d.get("tags") or []),
            default_outfit=d.get("defaultOutfit"),
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
class Situation:
    id: str
    name: str
    rating: str
    slots: list[str]
    canvas: dict
    moss_prompt: str
    anima_prompt: str
    tags: list[str] = field(default_factory=list)
    outfits: dict[str, str | None] = field(default_factory=dict)
    negative_moss: str = ""
    negative_anima: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Situation":
        return cls(
            id=d["id"], name=d["name"], rating=d["rating"], slots=list(d["slots"]),
            canvas=dict(d["canvas"]), moss_prompt=d["mossPrompt"], anima_prompt=d["animaPrompt"],
            tags=list(d.get("tags") or []), outfits=dict(d.get("outfits") or {}),
            negative_moss=d.get("negativeMoss") or "",
            negative_anima=d.get("negativeAnima") or "",
        )


@dataclass
class Scene:
    id: str
    situation: str
    cast: dict[str, str]
    name: str | None = None
    outfits: dict[str, str | None] = field(default_factory=dict)
    apply_negative: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            id=d["id"], situation=d["situation"], cast=dict(d["cast"]),
            name=d.get("name"), outfits=dict(d.get("outfits") or {}),
            apply_negative=bool(d.get("applyNegative", False)),
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
    outfit_source: str  # scene | situation | character | none_scene | none_situation | none_character | unset


@dataclass
class CompileResult:
    scene_id: str
    situation_id: str
    apply_negative: bool = False
    canvas: dict | None = None
    moss_prompt: str = ""
    anima_prompt: str = ""
    negative_moss: str = ""   # 合成済み（適用OFFでも常に入る）
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
