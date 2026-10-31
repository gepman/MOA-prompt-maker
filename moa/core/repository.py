"""data/ の5つのルートを再帰スキャンして、ルートごとの ID→パスの索引を作る（8章）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

from .filestamp import stamp as file_stamp
from .constants import ROOTS, SCHEMA_DIR
from .models import Action, Background, Character, FolderMeta, Issue, Outfit, Scene
from .validate import validate_repo

KINDS = tuple(ROOTS.values())
_BUILDERS = {
    "character": Character.from_dict,
    "outfit": Outfit.from_dict,
    "background": Background.from_dict,
    "action": Action.from_dict,
    "scene": Scene.from_dict,
}
_SCHEMA_FILES = {kind: f"{kind}.schema.json" for kind in KINDS} | {"folder": "folder.schema.json"}


@dataclass
class Entry:
    kind: str
    id: str
    path: Path
    obj: object
    raw: dict  # 読み込み時のYAML内容（アップロード照合用）
    stamp: tuple[int, int] | None = None  # 読み込み時の更新情報（外部変更の検知用）


def load_yaml(path: Path) -> object:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def iter_yaml_files(folder: Path):
    """配下の .yaml を名前順に返す。名前が `_` または `.` で始まるフォルダはスキャンしない。

    （`_folder.yaml` のようなファイルは対象。除外されるのはフォルダだけ。）
    """
    for child in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if child.is_dir():
            if child.name.startswith(("_", ".")):
                continue
            yield from iter_yaml_files(child)
        elif child.suffix == ".yaml":
            yield child


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
        for root_name, kind in ROOTS.items():
            base = self.root / root_name
            if base.is_dir():
                for path in iter_yaml_files(base):
                    self._load_file(kind, path)
        self.issues += validate_repo(self)

    def schema_errors(self, kind: str, data: dict) -> list[str]:
        errs = sorted(self._validators[kind].iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
        return [f"{'.'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errs]

    _schema_errors = schema_errors  # 旧名

    def _load_file(self, kind: str, path: Path) -> None:
        try:
            data = load_yaml(path)
        except Exception as e:  # YAML構文エラーなど
            self.issues.append(Issue("error", f"YAMLを読み込めません: {e}", path))
            return
        if not isinstance(data, dict):
            self.issues.append(Issue("error", "トップレベルがマッピングではありません", path))
            return

        if path.name == "_folder.yaml":
            errs = self.schema_errors("folder", data)
            if errs:
                self.issues += [Issue("error", f"スキーマ違反 {m}", path) for m in errs]
            else:
                self.folder_meta[path.parent] = FolderMeta.from_dict(data)
            return

        errs = self.schema_errors(kind, data)
        if errs:
            self.issues += [Issue("error", f"スキーマ違反 {m}", path) for m in errs]
            return

        eid = data["id"]
        if eid != path.stem:
            self.issues.append(Issue("error", f"id '{eid}' とファイル名 '{path.name}' が一致しません", path))
            return
        if eid in self.entries[kind]:  # 重複は同じルートの中だけで判定する（ルートが違えば同じ id でよい）
            other = self.entries[kind][eid].path
            self.issues.append(Issue("error", f"id '{eid}' が重複しています（{other.name}）", path))
            return

        entry = Entry(kind, eid, path, _BUILDERS[kind](data), data, file_stamp(path))
        self.entries[kind][eid] = entry
        self.by_path[path] = entry

    # ---- 参照 ----
    def ids(self, kind: str) -> list[str]:
        return list(self.entries[kind])

    def entry(self, kind: str, eid: str) -> Entry | None:
        return self.entries[kind].get(eid)

    def _obj(self, kind: str, eid: str):
        e = self.entries[kind].get(eid)
        return e.obj if e else None

    def character(self, eid: str) -> Character | None:
        return self._obj("character", eid)

    def outfit(self, eid: str) -> Outfit | None:
        return self._obj("outfit", eid)

    def background(self, eid: str) -> Background | None:
        return self._obj("background", eid)

    def action(self, eid: str) -> Action | None:
        return self._obj("action", eid)

    def scene(self, eid: str) -> Scene | None:
        return self._obj("scene", eid)

    def path_of(self, kind: str, eid: str) -> Path | None:
        e = self.entries[kind].get(eid)
        return e.path if e else None

    def root_dir(self, kind: str) -> Path:
        """kind のルートフォルダ（例 scene → data/scenes）。"""
        return self.root / next(name for name, k in ROOTS.items() if k == kind)

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)
