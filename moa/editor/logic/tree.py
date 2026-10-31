"""ツリー（パンくず＋一覧）とフォルダ作成（EDITOR_SPEC 2章・3章）。"""
from __future__ import annotations

from moa.core.folders import create_folder
from moa.core.repository import Repository
from moa.core.tree import breadcrumb, list_folder
from moa.core.usage import scenes_using_action, scenes_using_background

from .session import cwd_path


def listing(repo: Repository, st: dict) -> tuple[list[tuple[str, str]], str]:
    """(Radio の選択肢[(表示, 値)], パンくず)。使用中の Background / Action には Scene の件数を併記する。"""
    root = (repo.root / st["section"]).resolve()
    cwd = cwd_path(repo, st)
    choices: list[tuple[str, str]] = []
    if cwd != root:
        choices.append(("⬆ 上へ", "up"))
    for it in list_folder(repo, cwd):
        if it.kind == "folder":
            choices.append((it.label, "dir:" + repo.rel(it.path)))
            continue
        label = it.label
        users = {"background": scenes_using_background, "action": scenes_using_action}.get(it.entry.kind)
        n = len(users(repo, it.entry.id)) if users else 0
        if n:
            label += f"・Scene {n}件"
        choices.append((label, "file:" + repo.rel(it.path)))
    crumbs = " / ".join(label for label, _ in breadcrumb(repo, root, cwd))
    return choices, crumbs


def make_folder(repo: Repository, st: dict, name: str) -> tuple[str, str]:
    """今開いているフォルダの中に作る。(メッセージ, 作ったフォルダの相対パス or "")。"""
    root = (repo.root / st["section"]).resolve()
    try:
        path = create_folder(root, cwd_path(repo, st), name.strip() if name else name)
    except ValueError as e:
        return f"⚠ {e}", ""
    return f"📁 フォルダを作りました：{path}", repo.rel(path)
