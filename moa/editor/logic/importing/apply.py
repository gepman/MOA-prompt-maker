"""書き込み（EDITOR_SPEC 15.2・15.7章）。

書き込む直前に、索引を読み直して計画を作り直す（確認画面を開いている間に、同じ id が保存された場合などに備える）。
確認画面の内容と食い違うものは書き込まず、結果に出す。既存のファイルは決して上書きしない。
"""
from __future__ import annotations

import re

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from moa.core.entity_io import save_folder_meta
from moa.core.repository import Repository
from moa.core.yaml_io import atomic_create_text, make_yaml

from .inspection import Inspection
from .plan import F_CREATE, F_MERGE, F_REJECT, REJECT, Choices, Item, Plan, resolve

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


def rewrite_id(text: str, old_id: str, new_id: str) -> str:
    """いちばん上の階層の `id` の値だけを書き換える。それ以外のテキスト（コメント・書式・改行）はそのまま残す。

    ruamel の位置情報で値の場所を特定し、書き換えたあとに読み直して「id 以外が変わっていない」ことを確かめる。
    確かめられなければ ValueError。
    """
    doc = make_yaml().load(text)
    if not isinstance(doc, CommentedMap) or "id" not in doc:
        raise ValueError("トップレベルの id が見つかりません")
    line, col = doc.lc.value("id")
    lines = _LINE.findall(text)
    if line >= len(lines):
        raise ValueError("id の値の位置を特定できません")
    row = lines[line]
    seg = row[col:]
    quote = seg[0] if seg[:1] in ('"', "'") else ""
    expected = f"{quote}{old_id}{quote}"
    if not seg.startswith(expected) or (not quote and re.match(r"[A-Za-z0-9_]", seg[len(old_id):][:1] or " ")):
        raise ValueError("id の値の位置を特定できません")
    lines[line] = row[:col] + f"{quote}{new_id}{quote}" + seg[len(expected):]
    new_text = "".join(lines)
    before, after = YAML(typ="safe").load(text), YAML(typ="safe").load(new_text)
    if after != {**before, "id": new_id}:
        raise ValueError("id を書き換えたあとの内容を確認できませんでした（id 以外が変わっています）")
    return new_text


def _reject(it: Item, reason: str) -> None:
    it.action, it.reason, it.dest = REJECT, reason, None


def apply_plan(repo: Repository, insp: Inspection, choices: Choices, confirmed: Plan | None = None) -> Plan:
    """計画を書き込む。結果（書き込めなかったものを却下に直した計画）を返す。

    confirmed は、確認画面に出していた計画。あれば、**そのときの選択**で書き込み直前の状態から計画を作り直して比べ、
    食い違うもの（確認画面を開いている間に、同じ id が保存された等）は書かない。確認画面のあとに選択を変えたこと
    （種類・別 ID など）は食い違いとはみなさず、今の選択で書く。
    """
    repo.reload()
    cands = {c.path: c for c in insp.candidates}

    stale_items: dict[str, str] = {}
    stale_folders: set[tuple[str, str]] = set()
    if confirmed is not None:
        same_choices = resolve(repo, insp, confirmed.choices)   # 確認画面と同じ選択で、今の状態の計画
        before = {i.path: (i.action, i.final_id, i.dest) for i in confirmed.items}
        for it in same_choices.items:
            if before.get(it.path) != (it.action, it.final_id, it.dest):
                stale_items[it.path] = "確認画面を開いたあとに状況が変わったため、取り込みませんでした"
        before_f = {(f.path, f.key): f.action for f in confirmed.folders}
        for f in same_choices.folders:
            if before_f.get((f.path, f.key)) != f.action:
                stale_folders.add((f.path, f.key))
    plan = resolve(repo, insp, choices)

    for it in plan.items:
        if not it.writes:
            continue
        if it.path in stale_items:
            _reject(it, stale_items[it.path])
            continue
        try:
            text = cands[it.path].text
            if it.final_id != it.id:
                text = rewrite_id(text, it.id, it.final_id)
            atomic_create_text(it.dest, text)
        except FileExistsError:
            _reject(it, "書き込み先に同名のファイルがあるため、取り込みませんでした（既存は上書きしません）")
        except ValueError as e:
            _reject(it, f"別 ID に書き換えられませんでした：{e}")
        except OSError as e:
            _reject(it, f"書き込めませんでした：{e}")

    for f in plan.folders:
        if not f.writes:
            continue
        if (f.path, f.key) in stale_folders:
            f.action, f.reason = F_REJECT, "確認画面を開いたあとに状況が変わったため、取り込みませんでした"
            continue
        try:
            if f.action == F_CREATE:
                atomic_create_text(f.dest_dir / "_folder.yaml", f.text)
            elif f.action == F_MERGE:
                m = f.merged
                save_folder_meta(f.dest_dir, m["name"], m["description"], m["order"])
        except FileExistsError:
            f.action, f.reason = F_REJECT, "同名の _folder.yaml が既にあるため、取り込みませんでした（既存は上書きしません）"
        except OSError as e:
            f.action, f.reason = F_REJECT, f"書き込めませんでした：{e}"
    repo.reload()
    return plan
