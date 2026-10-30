"""服装の解決（5.2章）。三値：キーなし＝委譲 / `none` / Outfit の id。`null` は不正（スキーマエラー）。"""
from __future__ import annotations

from dataclasses import dataclass

from .constants import NONE


@dataclass(frozen=True)
class OutfitResolution:
    outfit_id: str | None
    source: str  # scene | action | character | none_scene | none_action | none_character | unset


def resolve_outfit(
    slot: str,
    cast_outfit: str | None,
    action_outfits: dict[str, str],
    default_outfit: str | None,
) -> OutfitResolution:
    """Scene.cast.<slot>.outfit → Action.outfits.<slot> → Character.defaultOutfit → なし の順で解決する。

    いずれのレイヤーも、値がなければ（None なら）下のレイヤーに委譲する。
    """
    for layer, value in (("scene", cast_outfit), ("action", action_outfits.get(slot)),
                         ("character", default_outfit)):
        if value is None:
            continue
        if value == NONE:
            return OutfitResolution(None, f"none_{layer}")
        return OutfitResolution(value, layer)
    return OutfitResolution(None, "unset")
