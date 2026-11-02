"""シーン保存ブロックの状態遷移（SPEC 10.2章）。Gradio に依存しない純粋関数（テスト可能）。

保存は「開く」とは別の操作で、共通ピッカー（moa.ui.logic の nav）とは別の状態 `sv` を持つ。

  sv = {"key": 初期値を作り直す条件（開き直し・Action の切り替え）,
        "cwd": 今表示しているフォルダ（data からの相対パス。scenes/ からはみ出さない）,
        "last_dir": このタブで直前に開いた／保存した Scene のフォルダ（ファイルには書かない）,
        "selected": 一覧で選んだ既存 Scene のパス（別名保存で、そのファイルだけ上書きを許すため）,
        "pending": 確認待ち（上書きの確認）か、外部変更で止まった保存}
"""
from __future__ import annotations

import copy
from pathlib import Path

from moa.core import Repository, validate_scene_id
from moa.core.folders import create_folder
from moa.core.tree import breadcrumb, list_folder
from moa.ui.logic import SaveTarget


def new_sv() -> dict:
    return {"key": None, "cwd": "scenes", "last_dir": None, "selected": None, "pending": None}


def scenes_root(repo: Repository) -> Path:
    return repo.root_dir("scene").resolve()


def cwd_path(repo: Repository, sv: dict) -> Path:
    """表示フォルダ。scenes/ の外や、消えたフォルダは scenes/ に戻す。"""
    root = scenes_root(repo)
    path = (repo.root / sv["cwd"]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return root
    return path if path.is_dir() else root


def _inside(repo: Repository, path: Path) -> bool:
    try:
        path.resolve().relative_to(scenes_root(repo))
    except ValueError:
        return False
    return True


# ---- 初期値 ----
def opened_path(repo: Repository, sc: dict) -> Path | None:
    """今開いている Scene のファイル。開いていない（新規作成中）、または索引にないときは None。"""
    return repo.path_of("scene", sc["scene_id"]) if sc["scene_id"] else None


def scene_id_taken(repo: Repository, scene_id: str) -> bool:
    """索引にある id か、索引にない（読み込めない）ファイルが scenes/ のどこかに同名で存在する。"""
    return repo.entry("scene", scene_id) is not None or any(scenes_root(repo).rglob(f"{scene_id}.yaml"))


def default_scene_id(repo: Repository, sc: dict) -> str:
    """別名保存の id の初期値。`<元のid>_copy`（新規作成中は `<action id>__default`）。使用済みなら空いている最小の番号まで進める。"""
    if sc["scene_id"]:
        base = f"{sc['scene_id']}_copy"
    elif sc["action_id"]:
        base = f"{sc['action_id']}__default"
    else:
        return ""
    candidate, n = base, 1
    while scene_id_taken(repo, candidate):
        n += 1
        candidate = f"{base}{n}"
    return candidate


def sync(repo: Repository, sv: dict, sc: dict) -> tuple[dict, str | None]:
    """Scene を開き直した／Action を選び直したときだけ、フォルダと id の初期値を作り直す。

    Background・キャスト・服装を変えただけでは、手で入れた値を残す。
    戻り値は (新しい sv, id 欄に入れる初期値。変えないときは None)。
    """
    sv = copy.deepcopy(sv)
    folder_key = [sc["open_seq"], sc["scene_id"]]
    id_key = folder_key + [sc["action_id"]]
    old = sv["key"] or {}
    opened = opened_path(repo, sc)
    if opened is not None:
        sv["last_dir"] = repo.rel(opened.parent)
    if old.get("folder") != folder_key:
        sv["cwd"] = repo.rel(opened.parent) if opened is not None else (sv["last_dir"] or "scenes")
        sv["selected"], sv["pending"] = None, None
    new_id = default_scene_id(repo, sc) if old.get("id") != id_key else None
    sv["key"] = {"folder": folder_key, "id": id_key}
    return sv, new_id


# ---- 一覧（フォルダ・Scene）----
def listing(repo: Repository, sv: dict) -> tuple[str, list[tuple[str, str]], list[tuple[str, str]]]:
    """(パンくず, フォルダの選択肢[(表示, 値)], そのフォルダにある Scene の選択肢)。値は `up` / `dir:<相対パス>` / `file:<相対パス>`。"""
    root, cwd = scenes_root(repo), cwd_path(repo, sv)
    folders: list[tuple[str, str]] = [("⬆ 上へ", "up")] if cwd != root else []
    scenes: list[tuple[str, str]] = []
    for it in list_folder(repo, cwd):
        if it.kind == "folder":
            folders.append((it.label, "dir:" + repo.rel(it.path)))
        elif it.entry is not None and it.entry.kind == "scene":
            scenes.append((it.label, "file:" + repo.rel(it.path)))
    crumbs = " / ".join(label for label, _ in breadcrumb(repo, root, cwd))
    return crumbs, folders, scenes


def selected_value(repo: Repository, sv: dict) -> str | None:
    """一覧で選んでいる Scene の値（そのフォルダに表示されているときだけ）。"""
    if not sv["selected"]:
        return None
    path = (repo.root / sv["selected"]).resolve()
    return "file:" + repo.rel(path) if path.parent == cwd_path(repo, sv) else None


def _drop_confirm(pending: dict | None) -> dict | None:
    """行き先を変えたら、上書きの確認待ちは取り消す（外部変更で止まった保存は残す）。"""
    return None if pending and pending["kind"] == "confirm" else pending


def move(repo: Repository, sv: dict, choice: str | None) -> dict:
    """フォルダを1階層移動する。移動すると、一覧で選んだ Scene は解除する。"""
    if not choice:
        return sv
    sv = copy.deepcopy(sv)
    cwd = cwd_path(repo, sv)
    target = cwd.parent if choice == "up" else (repo.root / choice.partition(":")[2]).resolve()
    if _inside(repo, target) and target.is_dir():
        sv["cwd"] = repo.rel(target)
        sv["selected"] = None
        sv["pending"] = _drop_confirm(sv["pending"])
    return sv


def select(repo: Repository, sv: dict, choice: str | None) -> tuple[dict, str | None]:
    """一覧で既存 Scene を選ぶ。(sv, id 欄に入れる id)。フォルダも、そのファイルのフォルダに切り替わる。"""
    if not choice:
        return sv, None
    entry = repo.by_path.get((repo.root / choice.partition(":")[2]).resolve())
    if entry is None or entry.kind != "scene" or not _inside(repo, entry.path):
        return sv, None
    sv = copy.deepcopy(sv)
    sv["cwd"], sv["selected"] = repo.rel(entry.path.parent), repo.rel(entry.path)
    sv["pending"] = _drop_confirm(sv["pending"])
    return sv, entry.id


def make_folder(repo: Repository, sv: dict, name: str) -> str:
    """表示中のフォルダの中に、新しいフォルダを作る（EDITOR_SPEC 12章-3。名前の検証は 2章と同じ）。"""
    try:
        path = create_folder(scenes_root(repo), cwd_path(repo, sv), (name or "").strip())
    except ValueError as e:
        return f"⚠ {e}"
    return f"📁 フォルダを作りました：`{repo.rel(path)}/`（開くと、ここに保存できます）"


# ---- 保存の行き先 ----
def overwrite_target(repo: Repository, sc: dict) -> SaveTarget | None:
    """［上書き保存］の行き先。Scene を開いているときだけ（新規作成中は None）。"""
    path = opened_path(repo, sc)
    return SaveTarget(sc["scene_id"], path=path) if path is not None else None


def overwrite_label(repo: Repository, sc: dict) -> str:
    path = opened_path(repo, sc)
    if path is not None:
        return f"上書き先：`{repo.rel(path)}`"
    return "_（新規作成中のため、上書き保存はできません。「別名で保存」を使ってください）_"


def plan_saveas(repo: Repository, sv: dict, scene_id: str) -> tuple[str, object, str]:
    """［別名で保存］の判定。("new", 保存先フォルダ, id) / ("confirm", 上書きするファイル, id) / ("error", メッセージ, "")。

    id の形式と、scenes/ 全体での重複を確認する。上書きが許されるのは、一覧で選んだそのフォルダのそのファイルだけ。
    """
    try:
        sid = validate_scene_id(scene_id or "")
    except ValueError as e:
        return "error", f"⚠ {e}", ""
    existing = repo.entry("scene", sid)
    if existing is None:
        stray = next(iter(scenes_root(repo).rglob(f"{sid}.yaml")), None)   # 読み込めないが存在するファイル
        if stray is not None:
            return "error", f"⚠ id `{sid}` は使えません。同名のファイル `{repo.rel(stray)}` があります", ""
        return "new", cwd_path(repo, sv), sid
    chosen = (repo.root / sv["selected"]).resolve() if sv["selected"] else None
    if chosen is not None and chosen == existing.path.resolve() and chosen.parent == cwd_path(repo, sv):
        return "confirm", existing.path, sid
    return "error", (f"⚠ id `{sid}` は既に使われています（`{repo.rel(existing.path)}`）。"
                     "別の id にするか、上書きするなら一覧からその Scene を選んでください"), ""


# ---- 確認待ち・外部変更待ち ----
def pack(repo: Repository, target: SaveTarget, kind: str) -> dict:
    """行き先を、画面の状態（JSON にできる形）として持つ。"""
    return {"kind": kind, "id": target.scene_id,
            "path": repo.rel(target.path) if target.path else None,
            "folder": repo.rel(target.folder) if target.folder else None}


def unpack(repo: Repository, pending: dict | None) -> SaveTarget | None:
    if not pending:
        return None
    return SaveTarget(pending["id"], path=(repo.root / pending["path"]) if pending["path"] else None,
                      folder=(repo.root / pending["folder"]) if pending["folder"] else None)


def set_pending(sv: dict, pending: dict | None) -> dict:
    sv = copy.deepcopy(sv)
    sv["pending"] = pending
    return sv


def confirm_text(pending: dict | None) -> str:
    if not pending or pending["kind"] != "confirm":
        return ""
    return f"⚠ 既存の Scene `{pending['path']}` を上書きします。よろしいですか？"
