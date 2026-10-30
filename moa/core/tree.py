"""共有ブラウザ用のフォルダ一覧（8章：_folder.yaml の order を優先、残りは自然順）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .repository import Entry, Repository
from .textutil import natural_key


@dataclass(frozen=True)
class TreeItem:
    kind: str  # "folder" | "file"
    label: str
    path: Path
    entry: Entry | None = None


def folder_label(repo: Repository, folder: Path) -> str:
    meta = repo.folder_meta.get(folder)
    return (meta.name if meta and meta.name else folder.name)


def entry_label(entry: Entry) -> str:
    name = getattr(entry.obj, "name", None) or entry.id
    icon = "🎬" if entry.kind == "scene" else "📄"
    return f"{icon} {name}（{entry.path.name}）"


def list_folder(repo: Repository, folder: Path) -> list[TreeItem]:
    if not folder.is_dir():
        return []
    items: list[TreeItem] = []
    for child in folder.iterdir():
        if child.is_dir():
            if child.name.startswith((".", "_")):  # 名前が `_` / `.` で始まるフォルダはスキャンしない（8章）
                continue
            items.append(TreeItem("folder", f"📁 {folder_label(repo, child)}", child))
        elif child.name.startswith("."):
            continue
        elif child.suffix == ".yaml" and child.name != "_folder.yaml":
            entry = repo.by_path.get(child.resolve())
            if entry is not None:
                items.append(TreeItem("file", entry_label(entry), child, entry))

    meta = repo.folder_meta.get(folder)
    order = {name: i for i, name in enumerate(meta.order)} if meta else {}
    items.sort(key=lambda it: (
        order.get(it.path.name, len(order)),
        0 if it.kind == "folder" else 1,
        natural_key(it.path.name),
    ))
    return items


def breadcrumb(repo: Repository, section_root: Path, folder: Path) -> list[tuple[str, Path]]:
    """section_root から folder までの (表示名, パス)。先頭はセクション名。"""
    crumbs = [(section_root.name, section_root)]
    try:
        parts = folder.relative_to(section_root).parts
    except ValueError:
        return crumbs
    cur = section_root
    for p in parts:
        cur = cur / p
        crumbs.append((folder_label(repo, cur), cur))
    return crumbs
