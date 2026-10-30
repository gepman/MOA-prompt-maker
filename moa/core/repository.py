"""data/ を再帰スキャンして ID→パスの索引を作る（8章）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

from .constants import NONE, SCHEMA_DIR
from .models import Character, FolderMeta, Issue, Outfit, Scene, Situation
from .validate import validate_repo

KINDS = ("character", "outfit", "situation", "scene")
_SECTIONS = {"characters": "character", "outfits": "outfit", "situations": None}
_BUILDERS = {
    "character": Character.from_dict,
    "outfit": Outfit.from_dict,
    "situation": Situation.from_dict,
    "scene": Scene.from_dict,
}
_SCHEMA_FILES = {
    "character": "character.schema.json",
    "outfit": "outfit.schema.json",
    "situation": "situation.schema.json",
    "scene": "scene.schema.json",
    "folder": "folder.schema.json",
}


@dataclass
class Entry:
    kind: str
    id: str
    path: Path
    obj: object
    raw: dict  # 読み込み時のYAML内容（アップロード照合用）


def is_scene_dict(d: dict) -> bool:
    """situations/ 配下の判定規則：`situation` と `cast` の両方を持てばScene（9.1章）。"""
    return "situation" in d and "cast" in d


def load_yaml(path: Path) -> object:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


class Repository:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self._validators = {
            k: Draft202012Validator(json.loads((SCHEMA_DIR / f).read_text(encoding="utf-8")))
            for k, f in _SCHEMA_FILES.items()
        }
        self.entries: dict[str, dict[str, Entry]] = {k: {} for k in KINDS}
        self.by_path: dict[Path, Entry] = {}
        self.folder_meta: dict[Path, FolderMeta] = {}
        self.issues: list[Issue] = []
        self.reload()

    # ---- 読み込み ----
    def reload(self) -> None:
        self.entries = {k: {} for k in KINDS}
        self.by_path = {}
        self.folder_meta = {}
        self.issues = []
        for section in _SECTIONS:
            base = self.root / section
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.yaml"), key=lambda p: str(p).casefold()):
                self._load_file(section, path)
        self.issues += validate_repo(self)

    def _schema_errors(self, kind: str, data: dict) -> list[str]:
        errs = sorted(self._validators[kind].iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
        return [f"{'.'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errs]

    def _load_file(self, section: str, path: Path) -> None:
        try:
            data = load_yaml(path)
        except Exception as e:  # YAML構文エラーなど
            self.issues.append(Issue("error", f"YAMLを読み込めません: {e}", path))
            return
        if not isinstance(data, dict):
            self.issues.append(Issue("error", "トップレベルがマッピングではありません", path))
            return

        if path.name == "_folder.yaml":
            errs = self._schema_errors("folder", data)
            if errs:
                self.issues += [Issue("error", f"スキーマ違反 {m}", path) for m in errs]
            else:
                self.folder_meta[path.parent] = FolderMeta.from_dict(data)
            return

        kind = _SECTIONS[section] or ("scene" if is_scene_dict(data) else "situation")
        errs = self._schema_errors(kind, data)
        if errs:
            self.issues += [Issue("error", f"スキーマ違反 {m}", path) for m in errs]
            return

        eid = data["id"]
        if eid != path.stem:
            self.issues.append(Issue("error", f"id '{eid}' とファイル名 '{path.name}' が一致しません", path))
            return
        if kind == "outfit" and eid == NONE:
            self.issues.append(Issue("error", f"服装idに予約語 '{NONE}' は使えません", path))
            return
        if eid in self.entries[kind] or (kind in ("situation", "scene") and eid in self.entries["situation" if kind == "scene" else "scene"]):
            other = self.path_of_any(eid)
            self.issues.append(Issue("error", f"id '{eid}' が重複しています（{other.name if other else '?'}）", path))
            return

        entry = Entry(kind, eid, path, _BUILDERS[kind](data), data)
        self.entries[kind][eid] = entry
        self.by_path[path] = entry

    # ---- 参照 ----
    def ids(self, kind: str) -> list[str]:
        return list(self.entries[kind])

    def entry(self, kind: str, eid: str) -> Entry | None:
        return self.entries[kind].get(eid)

    def character(self, eid: str) -> Character | None:
        e = self.entries["character"].get(eid)
        return e.obj if e else None

    def outfit(self, eid: str) -> Outfit | None:
        e = self.entries["outfit"].get(eid)
        return e.obj if e else None

    def situation(self, eid: str) -> Situation | None:
        e = self.entries["situation"].get(eid)
        return e.obj if e else None

    def scene(self, eid: str) -> Scene | None:
        e = self.entries["scene"].get(eid)
        return e.obj if e else None

    def path_of(self, kind: str, eid: str) -> Path | None:
        e = self.entries[kind].get(eid)
        return e.path if e else None

    def path_of_any(self, eid: str) -> Path | None:
        for kind in KINDS:
            if eid in self.entries[kind]:
                return self.entries[kind][eid].path
        return None

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)
