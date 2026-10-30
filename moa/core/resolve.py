from __future__ import annotations

from dataclasses import dataclass

from .constants import NONE


@dataclass(frozen=True)
class OutfitResolution:
    outfit_id: str | None
    source: str  # scene | situation | character | none_scene | none_situation | none_character | unset


def _spec(value: str | None) -> str | None:
    """キーなし／null／空文字は「未指定」（5章）。"""
    if value is None:
        return None
    value = value.strip()
    return value or None


def resolve_outfit(
    slot: str,
    scene_outfits: dict[str, str | None],
    situation_outfits: dict[str, str | None],
    default_outfit: str | None,
) -> OutfitResolution:
    """Scene → Situation → Character.defaultOutfit → なし の順で解決する。"""
    for layer, outfits in (("scene", scene_outfits), ("situation", situation_outfits)):
        value = _spec(outfits.get(slot))
        if value is None:
            continue
        if value == NONE:
            return OutfitResolution(None, f"none_{layer}")
        return OutfitResolution(value, layer)
    value = _spec(default_outfit)
    if value is None:
        return OutfitResolution(None, "unset")
    if value == NONE:
        return OutfitResolution(None, "none_character")
    return OutfitResolution(value, "character")
