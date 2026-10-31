"""索引の一部をドラフトで差し替えたリポジトリの見え方（EDITOR_SPEC 5章）。

`compile_scene` と `validate_*` は Repository の参照メソッドしか使わないので、そのまま渡せる。
ドラフトは新規（未保存の新 id）でもよい。元の Repository と保存ファイルには触らない。
"""
from __future__ import annotations

from pathlib import Path

from .repository import Entry, Repository


class OverlayRepository:
    def __init__(self, base: Repository, drafts: dict[tuple[str, str], object] | None = None):
        self.base = base
        self.drafts = dict(drafts or {})
        self.root = base.root
        self.issues = base.issues
        self.folder_meta = base.folder_meta

    def _obj(self, kind: str, eid: str):
        if (kind, eid) in self.drafts:
            return self.drafts[(kind, eid)]
        return self.base._obj(kind, eid)

    def character(self, eid):
        return self._obj("character", eid)

    def outfit(self, eid):
        return self._obj("outfit", eid)

    def background(self, eid):
        return self._obj("background", eid)

    def action(self, eid):
        return self._obj("action", eid)

    def scene(self, eid):
        return self._obj("scene", eid)

    def ids(self, kind: str) -> list[str]:
        ids = self.base.ids(kind)
        return ids + [i for k, i in self.drafts if k == kind and i not in ids]

    def entry(self, kind: str, eid: str) -> Entry | None:
        return self.base.entry(kind, eid)

    def path_of(self, kind: str, eid: str) -> Path | None:
        return self.base.path_of(kind, eid)

    def root_dir(self, kind: str) -> Path:
        return self.base.root_dir(kind)

    def rel(self, path: Path) -> str:
        return self.base.rel(path)
