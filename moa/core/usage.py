"""「使用中」の判定（EDITOR_SPEC 1b / 1c章）。"""
from __future__ import annotations

from .repository import Entry, Repository


def scenes_using_background(repo: Repository, bg_id: str) -> list[Entry]:
    """Scene の `background` がこの id のもの（Action の推奨背景は含めない）。"""
    return [e for e in repo.entries["scene"].values() if e.obj.background == bg_id]


def scenes_using_action(repo: Repository, action_id: str) -> list[Entry]:
    return [e for e in repo.entries["scene"].values() if e.obj.action == action_id]


def scenes_using_character(repo: Repository, char_id: str) -> list[Entry]:
    return [e for e in repo.entries["scene"].values()
            if any(c.character == char_id for c in e.obj.cast.values())]
