"""保存の流れ（EDITOR_SPEC 6章・1c章・9章）：検証 → 外部変更の検知 → 確認 → 書き込み。"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from moa.core.entity_io import save_entity, save_folder_meta
from moa.core.filestamp import ExternalChangeError, stamp
from moa.core.models import Issue, has_error
from moa.core.repository import Entry, Repository
from moa.core.usage import scenes_using_action, scenes_using_background

from . import forms
from .validation import check_new_id, validate_draft

# status
ERRORS = "errors"
EXTERNAL = "external_change"
CONFIRM_COW = "confirm_cow"             # 使用中の Background：複製として保存する確認
CONFIRM_USED_ACTION = "confirm_used_action"   # 使用中の Action：参照 Scene の一覧を見せる確認
COW_ID_ERROR = "cow_id_error"
SAVED = "saved"


@dataclass
class SaveOutcome:
    status: str
    message: str = ""
    issues: list[Issue] = field(default_factory=list)
    scenes: list[tuple[str, str]] = field(default_factory=list)   # (表示名, id)
    default_new_id: str = ""      # confirm_cow の新 ID 欄の初期値
    path: Path | None = None
    state: dict | None = None     # 保存後の状態（saved のとき）


def _scene_list(entries: list[Entry]) -> list[tuple[str, str]]:
    return [(e.obj.name or e.id, e.id) for e in entries]


def _after_save(repo: Repository, st: dict, kind: str, values: dict, path: Path) -> dict:
    """保存後は通常の編集画面（ID は読み取り専用）に切り替える。"""
    repo.reload()
    st = copy.deepcopy(st)
    st.update(mode="edit", kind=kind, id=values["id"], path=repo.rel(path),
              stamp=list(stamp(path) or ()) or None, loaded=forms.normalize(kind, values), base=None,
              cwd=repo.rel(path.parent), pending=None)
    return st


def save_draft(repo: Repository, st: dict, values: dict, *, confirmed: bool = False, force: bool = False,
               cow_id: str | None = None) -> SaveOutcome:
    """st["mode"] が edit / new / copy のときの保存。

    confirmed … 確認欄（COW・使用中 Action）で［保存］／［複製として保存］を押した。
    force     … 外部変更の2択で［上書き保存］を押した。
    cow_id    … 使用中 Background の複製保存の新 ID。
    """
    kind, mode = st["kind"], st["mode"]
    is_new = mode in ("new", "copy")
    v = forms.normalize(kind, values if is_new else {**values, "id": st["id"]})   # 編集中の ID は変えられない
    issues = validate_draft(repo, kind, v, new=is_new, original_id=st["id"])
    if has_error(issues):
        return SaveOutcome(ERRORS, "エラーがあるため保存できません", issues)

    path = None if is_new else repo.root / st["path"]
    if not is_new and not force:
        expected = tuple(st["stamp"]) if st["stamp"] else None
        if stamp(path) != expected:
            return SaveOutcome(EXTERNAL, "ファイルが外部で変更されています", issues)

    target_id, target_path, folder = v["id"], path, None
    if is_new:
        folder = (repo.root / st["save_dir"]).resolve()
    elif kind == "background":
        users = scenes_using_background(repo, st["id"])
        if users:
            if not confirmed:
                return SaveOutcome(CONFIRM_COW, f"この背景は {len(users)} 件の Scene で使われているため、複製として保存します",
                                   issues, _scene_list(users), f"{st['id']}_copy")
            cow = (cow_id or "").strip()
            bad = check_new_id(repo, "background", cow)
            if bad:
                return SaveOutcome(COW_ID_ERROR, bad[0].message, issues, _scene_list(users), cow)
            v, target_id, target_path, folder = {**v, "id": cow}, cow, None, path.parent
    elif kind == "action":
        users = scenes_using_action(repo, st["id"])
        if users and not confirmed:
            return SaveOutcome(CONFIRM_USED_ACTION,
                               f"この行為は {len(users)} 件の Scene で使われています。上書き保存します",
                               issues, _scene_list(users))

    try:
        written = save_entity(repo, kind, v, path=target_path, folder=folder)
    except ExternalChangeError:
        return SaveOutcome(EXTERNAL, "ファイルが外部で変更されています", issues)
    except ValueError as e:
        return SaveOutcome(ERRORS, str(e), issues + [Issue("error", str(e))])
    new_st = _after_save(repo, st, kind, v, written)
    return SaveOutcome(SAVED, f"保存しました：{written}", [i for i in issues if i.level == "warning"],
                       path=written, state=new_st)


def save_folder(repo: Repository, st: dict, values: dict, *, force: bool = False) -> SaveOutcome:
    """`_folder.yaml` の保存。order に存在しない名前があれば警告（保存はできる）。"""
    folder = repo.root / st["folder"]
    order = [o.strip() for o in values["order"].splitlines() if o.strip()]
    issues = [Issue("warning", f"order の '{o}' は、このフォルダにありません", None, "order")
              for o in order if not (folder / o).exists()]
    expected = tuple(st["stamp"]) if st["stamp"] else None
    if not force and stamp(folder / "_folder.yaml") != expected:
        return SaveOutcome(EXTERNAL, "ファイルが外部で変更されています", issues)
    written = save_folder_meta(folder, values["name"], values["description"], order)
    repo.reload()
    st = copy.deepcopy(st)
    st.update(stamp=list(stamp(written) or ()) or None, loaded=dict(values))
    return SaveOutcome(SAVED, f"保存しました：{written}", issues, path=written, state=st)
