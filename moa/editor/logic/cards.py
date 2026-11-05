"""mossItems のカード式編集（EDITOR_SPEC 8章）。カードは最大 MAX_ITEMS（64）枚。おすすめは 8 件まで（文言だけ）。純粋関数で、新しいリストを返す。"""
from __future__ import annotations

import copy

from .forms import MAX_ITEMS, full_bbox, new_item

LABELS = ["object", "text"]   # 候補（自由入力もできる）


def can_add(items: list[dict]) -> bool:
    return len(items) < MAX_ITEMS


def add_hint(items: list[dict]) -> str:
    return "" if can_add(items) else f"最大{MAX_ITEMS}件です"


def add_item(items: list[dict], canvas: dict) -> list[dict]:
    if not can_add(items):
        return items
    return copy.deepcopy(items) + [new_item(canvas)]


def delete_item(items: list[dict], i: int) -> list[dict]:
    return [copy.deepcopy(it) for j, it in enumerate(items) if j != i]


def move_up(items: list[dict], i: int) -> list[dict]:
    out = copy.deepcopy(items)
    if 0 < i < len(out):
        out[i - 1], out[i] = out[i], out[i - 1]
    return out


def move_down(items: list[dict], i: int) -> list[dict]:
    out = copy.deepcopy(items)
    if 0 <= i < len(out) - 1:
        out[i], out[i + 1] = out[i + 1], out[i]
    return out


def set_full(items: list[dict], i: int, canvas: dict) -> list[dict]:
    """［全面］：キャンバス全体 `[-H/2, -W/2, H/2, W/2]`。"""
    out = copy.deepcopy(items)
    if 0 <= i < len(out):
        out[i]["bbox"] = full_bbox(canvas)
    return out


def update_item(items: list[dict], i: int, **fields) -> list[dict]:
    out = copy.deepcopy(items)
    out[i].update(fields)
    return out
