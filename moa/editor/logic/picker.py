"""エディタの共通ピッカー（EDITOR_SPEC 3章）。Gradio に依存しない純粋関数。

各選択欄の［選ぶ］で開き、フォルダ移動か名前検索で選ぶ。状態 pk（dict）：
  target … 選択先の欄の名前（閉じているとき None）。TARGETS のキー
  label  … 画面に出す「選択先：…」の欄の名前
  kind   … 選ぶエンティティの種類
  cwd    … 今いるフォルダ（data からの相対パス）
  query  … 名前検索の文字列（空ならフォルダ表示）
  last   … ルート名ごとに前回開いていたフォルダ
"""
from __future__ import annotations

import copy

from moa.core import picker as core
from moa.core.repository import Repository

from .session import SECTIONS

# 欄の名前 → (種類, 表示名, 空にできるか)。o0〜o7 は Action のスロット別 outfits
TARGETS: dict[str, tuple[str, str, bool]] = {
    "def": ("outfit", "defaultOutfit", False),
    "act_bg": ("background", "推奨背景", True),
    "pv_act": ("action", "プレビューの Action", True),
    "pv_char": ("character", "プレビューのキャラ", True),
    "pv_bg": ("background", "プレビューの Background", True),
    **{f"o{i}": ("outfit", f"outfits.slot{i}", False) for i in range(8)},
}
EMPTY_TEXT = {"act_bg": "（未設定）", "pv_act": "（自動）", "pv_char": "（自動）", "pv_bg": "（自動）"}
EMPTY_BUTTON = {"act_bg": "未設定にする", "pv_act": "自動にする", "pv_char": "自動にする", "pv_bg": "自動にする"}


def new_pk() -> dict:
    return {"target": None, "label": "", "kind": None, "cwd": "", "query": "", "last": {}}


def _root(repo: Repository, pk: dict):
    return (repo.root / SECTIONS[pk["kind"]]).resolve()


def open_picker(repo: Repository, pk: dict, target: str, current_id: str | None, label: str | None = None) -> dict:
    """［選ぶ］を押したとき。選択中の項目のフォルダ → 前回のフォルダ → ルートの順で開く。"""
    kind, default_label, _ = TARGETS[target]
    pk = copy.deepcopy(pk)
    pk.update({"target": target, "kind": kind, "label": label or default_label, "query": ""})
    root = _root(repo, pk)
    pk["cwd"] = repo.rel(core.initial_folder(repo, root, current_id, kind, pk["last"].get(SECTIONS[kind])))
    return pk


def close(pk: dict) -> dict:
    pk = copy.deepcopy(pk)
    pk.update({"target": None, "query": ""})
    return pk


def enter(repo: Repository, pk: dict, rel: str | None) -> dict:
    """サブフォルダ・パンくずを押したとき。検索中ならフォルダ表示に戻る。"""
    if pk["target"] is None or not rel:
        return pk
    pk = copy.deepcopy(pk)
    root = _root(repo, pk)
    pk["cwd"] = repo.rel(core.clamp_folder(root, repo.root / rel))
    pk["query"] = ""
    pk["last"][SECTIONS[pk["kind"]]] = pk["cwd"]
    return pk


def set_query(pk: dict, query: str | None) -> dict:
    pk = copy.deepcopy(pk)
    pk["query"] = (query or "").strip()
    return pk


def pick(repo: Repository, pk: dict, entity_id: str) -> dict:
    """選んだ項目のフォルダを前回のフォルダとして覚えて、閉じる。"""
    pk = copy.deepcopy(pk)
    entry = repo.entry(pk["kind"], entity_id)
    if entry is not None:
        root = _root(repo, pk)
        pk["last"][SECTIONS[pk["kind"]]] = repo.rel(core.clamp_folder(root, entry.path.parent))
    return close(pk)


def view(repo: Repository, pk: dict) -> dict:
    """画面に出す内容：target_md、crumbs / dirs / items の選択肢（[(表示, 値)]）、msg、cwd、can_clear、clear_text。"""
    target = pk["target"]
    if target is None:
        return {"target_md": "", "crumbs": [], "dirs": [], "items": [], "msg": "", "cwd": None,
                "can_clear": False, "clear_text": ""}
    root = _root(repo, pk)
    cwd = core.clamp_folder(root, repo.root / pk["cwd"] if pk["cwd"] else None)
    fv = core.folder_view(repo, root, cwd)
    out = {"target_md": f"**選択先：{pk['label']}**", "crumbs": fv.crumbs, "cwd": repo.rel(cwd),
           "can_clear": TARGETS[target][2], "clear_text": EMPTY_BUTTON.get(target, "")}
    if pk["query"]:
        rows, total = core.search(repo, root, pk["query"])
        out |= {"dirs": [], "items": [(f"{core.entity_label(e)}　📁 {where}", e.id) for e, where in rows],
                "msg": f"🔍 {pk['query']}：{core.search_message(len(rows), total)}"}
    else:
        out |= {"dirs": fv.folders, "items": fv.entities, "msg": f"{len(fv.entities)} 件（このフォルダ直下）"}
    return out


def shown(repo: Repository, kind: str, entity_id: str | None, *, mode: str = "outfit", empty: str = "（未設定）") -> str:
    """欄に出す文字列。mode は三値コントロール（unset / none / outfit）。"""
    if mode == "unset":
        return "未指定"
    if mode == "none":
        return "none（着せない）"
    if not entity_id:
        return empty
    entry = repo.entry(kind, entity_id)
    return core.entity_label(entry) if entry else f"{entity_id}（見つかりません）"
