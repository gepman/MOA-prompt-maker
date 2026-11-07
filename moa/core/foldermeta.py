"""`_folder.yaml`（4.6章）の編集の共通処理：フォームの値、未保存の判定、検証つきの保存。

ビルダー（scenes/ のフォルダ情報。SPEC 10.4章）とエディタ（4ルートのフォルダ編集。EDITOR_SPEC 2章）の両方が使う。
書き戻しは `entity_io.apply_folder_values`（変更した項目だけを差し替え、コメントとキー順を残す）、書き込みは一時ファイル→置き換え。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .entity_io import _plain, apply_folder_values, load_folder_doc
from .filestamp import stamp
from .models import Issue
from .repository import Repository
from .yaml_io import atomic_dump

SAVED = "saved"
EXTERNAL = "external_change"
ERRORS = "errors"


def folder_values(repo: Repository, folder: Path) -> dict:
    """フォルダの `_folder.yaml` をフォームの値にする。order は1行に1つの文字列（エディタのフォーム形式）。"""
    meta = repo.folder_meta.get(folder.resolve()) or repo.folder_meta.get(folder)
    return {"name": (meta.name if meta else None) or "", "description": (meta.description if meta else None) or "",
            "order": "\n".join(meta.order) if meta else ""}


def folder_dirty(current: dict, loaded: dict) -> bool:
    norm = lambda v: {"name": v["name"].strip(), "description": v["description"].strip(),  # noqa: E731
                      "order": [o.strip() for o in v["order"].splitlines() if o.strip()]}
    return norm(current) != norm(loaded)


@dataclass
class FolderSaveResult:
    status: str                      # SAVED | EXTERNAL | ERRORS
    message: str = ""
    issues: list[Issue] = field(default_factory=list)   # ERRORS のとき、保存を拒否した理由
    path: Path | None = None         # SAVED のとき、書いたファイル


def write_folder_meta(repo: Repository, folder: Path, name: str, description: str, order: list[str], *,
                      expected_stamp=None, force: bool = False) -> FolderSaveResult:
    """`_folder.yaml` を保存する。無ければ作る。全項目が空でも削除せず、空のマップのファイルとして残す。

    1. 外部変更の検知：読み込み時の更新情報（expected_stamp。ファイルが無かったときは None）と違えば EXTERNAL（force=True なら飛ばす）
    2. 既存の内容に name / description / order の変更だけを反映し、folder スキーマで検証する。違反なら書かずに ERRORS
    3. 一時ファイル→置き換えで書き、repo を読み直す
    """
    path = Path(folder) / "_folder.yaml"
    if not force and stamp(path) != (tuple(expected_stamp) if expected_stamp else None):
        return FolderSaveResult(EXTERNAL, "ファイルが外部で変更されています")
    try:
        doc = load_folder_doc(path)
    except Exception as e:   # YAML 構文エラーなど。壊れたファイルは上書きしない
        return FolderSaveResult(ERRORS, "既存の _folder.yaml を読み込めません",
                                [Issue("error", f"YAMLを読み込めません: {e}", path)])
    apply_folder_values(doc, name, description, order)
    errors = repo.schema_errors("folder", _plain(doc))
    if errors:
        return FolderSaveResult(ERRORS, "folder スキーマに合わないため保存できません",
                                [Issue("error", f"スキーマ違反 {m}", path) for m in errors])
    atomic_dump(doc, path)
    repo.reload()
    return FolderSaveResult(SAVED, f"保存しました：{path}", path=path)
