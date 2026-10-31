"""エディタの状態と遷移（EDITOR_SPEC 3章・7章）。Gradio に依存しない純粋関数。

状態 st（dict）：
  section … ルート名（characters / outfits / backgrounds / actions）
  cwd     … ツリーで開いているフォルダ（data からの相対パス）
  mode    … None / "edit" / "new" / "copy" / "folder"
  kind    … 編集中のエンティティの種別（folder のときは None）
  id, path, stamp … 編集中のファイル（edit）。stamp は読み込み時の更新情報
  loaded  … 読み込み時のフォーム値（未保存の変更の判定に使う）
  save_dir … 新規・複製の保存先フォルダ（data からの相対パス）
  folder  … folder モードで編集中のフォルダ（相対パス）
  pending … 未保存の変更があって止めた移動 {"action": ..., ...}
  last    … 種別ごとに最後に開いた項目の id（プレビューの［ツリーで選択中のものを使う］用）
"""
from __future__ import annotations

import copy
from pathlib import Path

from moa.core.filestamp import stamp
from moa.core.repository import Repository

from . import forms

SECTIONS = {"character": "characters", "outfit": "outfits", "background": "backgrounds", "action": "actions"}
KIND_OF = {v: k for k, v in SECTIONS.items()}


def new_state() -> dict:
    return {"section": "characters", "cwd": "characters", "mode": None, "kind": None, "id": None, "path": None,
            "stamp": None, "loaded": None, "save_dir": "characters", "folder": None, "pending": None, "last": {}}


def section_kind(st: dict) -> str:
    return KIND_OF[st["section"]]


def cwd_path(repo: Repository, st: dict) -> Path:
    """表示フォルダ。範囲外・消えたフォルダはセクションのルートに戻す。"""
    root = (repo.root / st["section"]).resolve()
    cwd = (repo.root / st["cwd"]).resolve()
    try:
        cwd.relative_to(root)
    except ValueError:
        return root
    return cwd if cwd.is_dir() else root


# ---- folder モードの値 ----
def folder_values(repo: Repository, folder: Path) -> dict:
    meta = repo.folder_meta.get(folder.resolve()) or repo.folder_meta.get(folder)
    return {"name": (meta.name if meta else None) or "", "description": (meta.description if meta else None) or "",
            "order": "\n".join(meta.order) if meta else ""}


def folder_dirty(current: dict, loaded: dict) -> bool:
    norm = lambda v: {"name": v["name"].strip(), "description": v["description"].strip(),  # noqa: E731
                      "order": [o.strip() for o in v["order"].splitlines() if o.strip()]}
    return norm(current) != norm(loaded)


def is_dirty(st: dict, values: dict | None) -> bool:
    if st["mode"] is None or values is None or st["loaded"] is None:
        return False
    if st["mode"] == "folder":
        return folder_dirty(values, st["loaded"])
    return forms.is_dirty(st["kind"], values, st["loaded"])


# ---- 開く ----
def _opened(st: dict, **kw) -> dict:
    st = copy.deepcopy(st)
    st.update({"mode": None, "kind": None, "id": None, "path": None, "stamp": None, "loaded": None,
               "folder": None, "pending": None})
    st.update(kw)
    return st


def open_entry(repo: Repository, st: dict, entry) -> dict:
    values = forms.values_of(entry.obj)
    st = {**st, "last": {**st["last"], entry.kind: entry.id}}
    return _opened(st, mode="edit", kind=entry.kind, id=entry.id, path=repo.rel(entry.path),
                   stamp=list(entry.stamp) if entry.stamp else None, loaded=values,
                   cwd=repo.rel(entry.path.parent))


def open_folder(repo: Repository, st: dict, folder: Path) -> dict:
    return _opened(st, mode="folder", folder=repo.rel(folder), loaded=folder_values(repo, folder), cwd=repo.rel(folder),
                   stamp=list(stamp(folder / "_folder.yaml") or ()) or None)


def start_new(repo: Repository, st: dict) -> dict:
    kind = section_kind(st)
    return _opened(st, mode="new", kind=kind, loaded=forms.new_values(kind), save_dir=repo.rel(cwd_path(repo, st)))


def start_copy(repo: Repository, st: dict, values: dict) -> dict:
    """複製して新規作成：新 ID の初期値は `<元のid>_copy`、保存先は複製元のフォルダ。"""
    if st["mode"] != "edit":
        return st
    src = (repo.root / st["path"]).parent
    return _opened(st, mode="copy", kind=st["kind"], loaded=forms.copy_values(values), save_dir=repo.rel(src))


# ---- 未保存の変更があるときは止める ----
def request(repo: Repository, st: dict, values: dict | None, action: dict) -> tuple[dict, bool]:
    """移動の要求。未保存の変更があれば pending を立てて (st, False)。なければそのまま実行して (st, True)。"""
    if is_dirty(st, values):
        st = copy.deepcopy(st)
        st["pending"] = action
        return st, False
    return perform(repo, st, action), True


def perform(repo: Repository, st: dict, action: dict) -> dict:
    kind = action["action"]
    if kind == "section":
        section = action["section"]
        return _opened(st, section=section, cwd=section, save_dir=section)
    if kind == "up":
        return _opened(st, cwd=repo.rel(cwd_path(repo, st).parent))
    if kind == "dir":
        return open_folder(repo, st, (repo.root / action["rel"]).resolve())
    if kind == "file":
        entry = repo.by_path.get((repo.root / action["rel"]).resolve())
        return open_entry(repo, st, entry) if entry else _opened(st)
    if kind == "new":
        return start_new(repo, st)
    if kind == "reload":
        repo.reload()
        return _reselect(repo, st)
    raise ValueError(kind)


def _reselect(repo: Repository, st: dict) -> dict:
    """再読み込み後に、編集中の項目を読み直す（無くなっていれば何も開かない）。"""
    if st["mode"] == "edit":
        entry = repo.entry(st["kind"], st["id"])
        return open_entry(repo, st, entry) if entry else _opened(st)
    if st["mode"] == "folder":
        return open_folder(repo, st, repo.root / st["folder"])
    return _opened(st)


def resolve_pending(repo: Repository, st: dict, discard: bool) -> dict:
    """［破棄して移動］（discard=True）／［キャンセル］。"""
    action = st["pending"]
    st = copy.deepcopy(st)
    st["pending"] = None
    return perform(repo, st, action) if discard and action else st


def reload_current(repo: Repository, st: dict) -> dict:
    """外部変更の検知後の［再読み込み（編集を破棄）］。"""
    repo.reload()
    return _reselect(repo, st)
