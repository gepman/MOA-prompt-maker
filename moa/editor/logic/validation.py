"""保存時の検証（EDITOR_SPEC 1章の表・1b章）。core の検証をドラフトに対してそのまま使う。"""
from __future__ import annotations

from moa.core.bbox import check_overflow
from moa.core.constants import ID_RE, NONE
from moa.core.models import Issue
from moa.core.overlay import OverlayRepository
from moa.core.repository import Repository
from moa.core.usage import scenes_using_action
from moa.core.validate import validate_action, validate_background, validate_character

from .forms import DEFAULT_CANVAS, normalize, to_obj, to_yaml_dict

DRAFT_ID = "__draft__"


def id_taken(repo: Repository, kind: str, eid: str) -> bool:
    """同じルートの索引全体に同じ id があるか。索引にない（読み込めない）ファイルが同名で存在する場合も含める。"""
    return repo.entry(kind, eid) is not None or any(repo.root_dir(kind).rglob(f"{eid}.yaml"))


def check_new_id(repo: Repository, kind: str, eid: str) -> list[Issue]:
    """新規 ID の形式・重複・Outfit の `none`。"""
    if not ID_RE.match(eid or ""):
        return [Issue("error", "ID は半角英小文字・数字・アンダースコアだけで入力してください（空は不可）", None, "id")]
    if kind == "outfit" and eid == NONE:
        return [Issue("error", "Outfit の id に `none` は使えません", None, "id")]
    if id_taken(repo, kind, eid):
        return [Issue("error", f"id '{eid}' は既に使われています（{kind}）", None, "id")]
    return []


def slot_issues(repo: Repository, original_id: str, new_slots: list[str]) -> list[Issue]:
    """1b章：参照中の Action では、スロットの削除・改名はエラー、追加は警告。"""
    old = repo.action(original_id)
    users = scenes_using_action(repo, original_id)
    if old is None or not users:
        return []
    names = "、".join(f"{e.obj.name or e.id}（{e.id}）" for e in users)
    issues = []
    removed = [s for s in old.slots if s not in new_slots]
    added = [s for s in new_slots if s not in old.slots]
    if removed:
        issues.append(Issue("error", f"スロット {', '.join(removed)} の削除・名前変更はできません"
                                     f"（次の Scene が参照しています：{names}）。追加だけ可能です", None, "slots"))
    if added:
        issues.append(Issue("warning", f"スロット {', '.join(added)} を追加すると、次の Scene は"
                                       f"「未割り当てスロット」でコンパイルエラーになります：{names}", None, "slots"))
    return issues


def validate_draft(repo: Repository, kind: str, values: dict, *, new: bool, original_id: str | None = None) -> list[Issue]:
    """編集中の内容を検証する。new=True は新規（複製を含む）で、id の形式・重複もここで見る。"""
    v = normalize(kind, values)
    issues: list[Issue] = []
    eid = v.get("id", "")
    if new:
        issues += check_new_id(repo, kind, eid)

    for msg in repo.schema_errors(kind, to_yaml_dict(kind, v)):
        field = msg.split(":", 1)[0]
        if new and field == "id":
            continue  # 新規 ID は check_new_id が報告済み
        issues.append(Issue("error", f"スキーマ違反 {msg}", None, None if field == "(root)" else field))
    if not v.get("name"):
        issues.append(Issue("error", "name を入力してください", None, "name"))
    if any(i.message.startswith("スキーマ違反") for i in issues):
        return issues  # 型が壊れているとオブジェクトを作れない

    obj = to_obj(kind, {**v, "id": eid or DRAFT_ID})
    ov = OverlayRepository(repo, {(kind, obj.id): obj})
    issues += object_issues(ov, kind, obj)
    if kind == "action" and not new and original_id:
        issues += slot_issues(repo, original_id, obj.slots)
    return issues


def object_issues(ov, kind: str, obj) -> list[Issue]:
    """オブジェクトができたあとの、種類別の意味チェック（EDITOR_SPEC 1章の表）。

    ov は、検証したいオブジェクトを含む Repository の見え方（OverlayRepository など）。
    保存時の検証（validate_draft）と、インポート（15章）が同じ関数を使う。Scene の検証は moa.core.validate にある。
    """
    issues: list[Issue] = []
    if kind == "character":
        issues += validate_character(ov, obj)
    elif kind == "background":
        issues += validate_background(ov, obj)
        if obj.canvas["w"] % 16 or obj.canvas["h"] % 16:
            issues.append(Issue("warning", f"canvas {obj.canvas['w']}×{obj.canvas['h']} は 16 の倍数ではありません",
                                None, "canvas"))
    elif kind == "action":
        issues += validate_action(ov, obj)
        bg = ov.background(obj.background) if obj.background else None
        canvas = bg.canvas if bg is not None else DEFAULT_CANVAS
        issues += check_overflow(obj.moss_items, canvas, f"Action '{obj.id}'", None)
    return issues
