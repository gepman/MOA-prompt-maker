"""ピッカー（フォルダ移動＋名前検索）の共通ロジック。ビルダーの共有ブラウザとエディタの共通ピッカーが使う。

並び順は SPEC 8章（`_folder.yaml` の order を先に、残りは自然順）。`moa.core.tree.list_folder` をそのまま使う。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .repository import Entry, Repository
from .tree import breadcrumb, list_folder

SEARCH_LIMIT = 100


@dataclass
class FolderView:
    crumbs: list[tuple[str, str]] = field(default_factory=list)    # (表示名, フォルダの相対パス)
    folders: list[tuple[str, str]] = field(default_factory=list)   # 直下のサブフォルダ (表示名, 相対パス)
    entities: list[tuple[str, str]] = field(default_factory=list)  # 直下のエンティティ ("名前（ID）", id)


def entity_label(entry: Entry) -> str:
    name = getattr(entry.obj, "name", None) or entry.id
    return f"{name}（{entry.id}）"


def clamp_folder(root: Path, path: Path | None) -> Path:
    """ルートの外・消えたフォルダはルートに戻す。"""
    root = Path(root).resolve()
    if path is None:
        return root
    path = Path(path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return root
    return path if path.is_dir() else root


def folder_view(repo: Repository, root: Path, folder: Path) -> FolderView:
    root = Path(root).resolve()
    folder = clamp_folder(root, folder)
    view = FolderView(crumbs=[(label, repo.rel(p)) for label, p in breadcrumb(repo, root, folder)])
    for it in list_folder(repo, folder):
        if it.kind == "folder":
            view.folders.append((it.label.removeprefix("📁 "), repo.rel(it.path)))
        else:
            view.entities.append((entity_label(it.entry), it.entry.id))
    return view


def walk(repo: Repository, folder: Path):
    """配下のエンティティを、表示順のまま深さ優先で返す。"""
    for it in list_folder(repo, folder):
        if it.kind == "folder":
            yield from walk(repo, it.path)
        else:
            yield it.entry


def path_label(repo: Repository, root: Path, entry: Entry) -> str:
    """所属フォルダのパス（表示名）。"""
    return " / ".join(label for label, _ in breadcrumb(repo, Path(root).resolve(), entry.path.parent))


def _matches(entry: Entry, q: str) -> bool:
    name = getattr(entry.obj, "name", None) or ""
    tags = getattr(entry.obj, "tags", None) or []
    return q in name.casefold() or q in entry.id.casefold() or any(q in t.casefold() for t in tags)


def search(repo: Repository, root: Path, query: str, limit: int = SEARCH_LIMIT) -> tuple[list[tuple[Entry, str]], int]:
    """ルート全体から name・ID・tags で部分一致。(先頭 limit 件の (entry, パス表示名), 全件数)。"""
    q = (query or "").strip().casefold()
    if not q:
        return [], 0
    root = Path(root).resolve()
    hits = [e for e in walk(repo, root) if _matches(e, q)]
    return [(e, path_label(repo, root, e)) for e in hits[:limit]], len(hits)


def search_message(shown: int, total: int, limit: int = SEARCH_LIMIT) -> str:
    if total == 0:
        return "該当なし"
    if total > shown:
        return f"全 {total} 件中、先頭 {limit} 件を表示"
    return f"{total} 件"


def initial_folder(repo: Repository, root: Path, current_id: str | None, kind: str,
                   last_rel: str | None) -> Path:
    """開いたときのフォルダ：選択中の項目のフォルダ → 前回のフォルダ → ルート。"""
    root = Path(root).resolve()
    entry = repo.entry(kind, current_id) if current_id else None
    if entry is not None:
        return clamp_folder(root, entry.path.parent)
    return clamp_folder(root, repo.root / last_rel if last_rel else None)
