"""結果の表示（EDITOR_SPEC 15.11章）と、確認画面の表（競合・種類・description・表示名）の組み立て／読み取り。

画面は部品の個数を変えない方針なので、可変個の一覧は1つの表（Dataframe）に入れる。ここは Gradio に依存しない。
"""
from __future__ import annotations

import difflib
from html import escape

from moa.core.diffcore import Row
from moa.core.repository import Repository
from moa.ui.diffhtml import rows_to_html

from .plan import (CREATE, F_CREATE, F_MERGE, F_REJECT, F_SKIP_LABEL, F_SKIP_SAME, OVERWRITE, REJECT, RENAMED,
                   SKIP_CHOICE, SKIP_SAME, SKIP_UNSELECTED, Choices, Plan)

RESULT_HEADERS = ["ファイル", "結果", "詳細"]
CONFLICT_HEADERS = ["ファイル", "種類", "id", "既存のファイル", "扱い", "別IDで取り込む", "新しいID"]
HANDLING_SKIP, HANDLING_OVERWRITE = "スキップ", "上書き"   # 「扱い」列（読み取り専用。上書きは中身を確認して OK を押したものだけ）
KINDS_HEADERS = ["ファイル", "適合する種類", "取り込む種類（選ぶ）"]
DESC_HEADERS = ["フォルダ", "既存の description", "取り込み側の description", "取り込み側を使う"]
LABEL_HEADERS = ["フォルダ", "取り込み側の name", "表示名を付ける"]


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


def _warn(warnings: list[str]) -> str:
    return "".join(f"　⚠ {w}" for w in warnings)


def result_rows(repo: Repository, plan: Plan, done: bool) -> list[list[str]]:
    """ファイルごとの結果。done=False は書き込み前（予定）、True は書き込み後。"""
    now = "取り込んだ" if done else "取り込む"
    rows: list[list[str]] = []
    for it in plan.items:
        if it.action == CREATE:
            result, detail = f"{now}（新規）", f"→ {repo.rel(it.dest)}"
        elif it.action == RENAMED:
            result, detail = f"{now}（別ID）", f"{it.id} → {it.final_id}：{repo.rel(it.dest)}"
        elif it.action == OVERWRITE:
            result, detail = f"{now}（上書き）", f"既存の {it.existing} を置き換え"
        elif it.action == SKIP_SAME:
            result, detail = "スキップ（同じ内容）", it.reason
        elif it.action == SKIP_CHOICE:
            result, detail = "スキップ（選択）", it.reason
        elif it.action == SKIP_UNSELECTED:
            result, detail = "スキップ（種類が未選択）", it.reason
        else:
            result, detail = "却下", it.reason
        if it.warnings:
            result += f"・警告 {len(it.warnings)} 件"
        rows.append([it.path, result, detail + _warn(it.warnings)])
    for f in plan.folders:
        shown = f"{f.path}" + (f"（{f.key}）" if f.key else "")
        if f.action == F_CREATE:
            result, detail = f"{now}（フォルダ設定・そのまま）", f"→ {repo.rel(f.dest_dir / '_folder.yaml')}"
        elif f.action == F_MERGE:
            result, detail = f"{now}（フォルダ設定・合成）", f"既存の _folder.yaml に合成：{f.key}"
        elif f.action == F_SKIP_SAME:
            result, detail = "スキップ（同じ内容）", f.reason
        elif f.action == F_SKIP_LABEL:
            result, detail = "スキップ（選択）", f.reason
        else:
            result, detail = "却下", f.reason
        rows.append([shown, result, detail])
    rows += [[ig.path, "無視", ig.reason] for ig in plan.ignored]
    return rows


def summary_md(plan: Plan, done: bool) -> str:
    if plan.error:
        return f"❌ **取り込めません**：{plan.error}"
    now = "取り込んだ" if done else "取り込む予定"
    count = lambda *acts: sum(1 for i in plan.items if i.action in acts)  # noqa: E731
    fcount = lambda *acts: sum(1 for f in plan.folders if f.action in acts)  # noqa: E731
    warns = sum(len(i.warnings) for i in plan.items if i.writes)
    lines = [f"**{now}：{count(CREATE, RENAMED, OVERWRITE)} 件**（別ID {count(RENAMED)} 件・上書き {count(OVERWRITE)} 件）＋フォルダ設定 {fcount(F_CREATE, F_MERGE)} 件"
             f" ／ スキップ {count(SKIP_SAME, SKIP_CHOICE, SKIP_UNSELECTED) + fcount(F_SKIP_SAME, F_SKIP_LABEL)} 件"
             f"（同じ内容 {count(SKIP_SAME) + fcount(F_SKIP_SAME)}・選択 {count(SKIP_CHOICE) + fcount(F_SKIP_LABEL)}"
             f"・種類が未選択 {count(SKIP_UNSELECTED)}）"
             f" ／ 却下 {count(REJECT) + fcount(F_REJECT)} 件 ／ 無視 {len(plan.ignored)} 件 ／ 警告 {warns} 件"]
    lines += [f"- {n}" for n in plan.notes]
    return "\n".join(lines)


# ---- 確認画面の表 ----
def conflict_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[i.path, i.kind, i.id, i.existing, HANDLING_OVERWRITE if i.path in choices.overwrite else HANDLING_SKIP,
             i.path in choices.rename,
             (choices.rename.get(i.path) or "").strip() or i.suggested] for i in plan.items if i.conflict]


def kinds_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[i.path, " / ".join(i.candidates), choices.kinds.get(i.path, "")] for i in plan.items if i.candidates and not i.kind
            and i.action != REJECT]


def desc_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[f.key, f.desc_conflict[0], f.desc_conflict[1], choices.desc.get(f.key) == "import"]
            for f in plan.folders if f.desc_conflict]


def label_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[f.key, f.import_name or "（name なし）", bool(choices.label.get(f.key))] for f in plan.folders if f.label_choice]


# ---- 競合ファイルの中身の確認（上書きする前に見るためのもの）----
PEEK_DIFF, PEEK_NEW, PEEK_OLD = "差分（既存 → 取り込み側）", "取り込み側の全文", "既存の全文"
PEEK_MODES = [PEEK_DIFF, PEEK_NEW, PEEK_OLD]
_CONTEXT, _FOLD = 3, 8   # 差分の前後に残す変更なしの行数 ／ これより長い変更なしの連続は畳む


def conflict_paths(plan: Plan) -> list[str]:
    return [i.path for i in plan.items if i.conflict]


def line_diff_rows(old: str, new: str) -> list[Row]:
    """テキストの行単位の差分。変更なしの長い連続は「… N 行 変更なし …」に畳む。同じなら空。"""
    a, b = old.splitlines(), new.splitlines()
    rows: list[Row] = []
    plain = lambda t: Row("inline", segs=[("eq", t)])   # noqa: E731
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            lines = a[i1:i2]
            if len(lines) > _FOLD:
                rows += [plain(t) for t in lines[:_CONTEXT]]
                rows.append(Row("head", text=f"… {len(lines) - 2 * _CONTEXT} 行 変更なし …"))
                rows += [plain(t) for t in lines[-_CONTEXT:]]
            else:
                rows += [plain(t) for t in lines]
        else:
            rows += [Row("del", text=t) for t in a[i1:i2]] + [Row("ins", text=t) for t in b[j1:j2]]
    return rows if any(r.kind in ("del", "ins") for r in rows) else []


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, AttributeError):
        return ""


def peek_html(plan: Plan, insp, path: str | None, mode: str) -> str:
    """競合ファイルの確認表示（15.6章）。差分・取り込み側の全文・既存の全文。画面に出す文字列はすべてエスケープする。"""
    it = plan.item(path) if path else None
    cand = next((c for c in insp.candidates if c.path == path), None) if insp is not None else None
    if it is None or not it.conflict or cand is None:
        return ""
    old, new = _read(it.existing_path), cand.text
    head = f"<div><b>{escape(path)}</b> ／ 既存：{escape(it.existing)}</div>"
    box = '<div style="white-space:pre-wrap;font-family:monospace;overflow-x:auto">{}</div>'
    if mode == PEEK_NEW:
        return head + box.format(escape(new))
    if mode == PEEK_OLD:
        return head + box.format(escape(old))
    rows = line_diff_rows(old, new)
    body = rows_to_html(rows) if rows else "<div>差分はありません（コメント・空白・改行コードだけの違いかもしれません）</div>"
    return head + box.format(body)


def _rows(table) -> list[list]:
    """Dataframe（type="array"）の値から、中身のある行だけを取り出す。"""
    if table is None:
        return []
    if isinstance(table, dict):   # Gradio が持つ表の値（{"headers": …, "data": [[…]]}）
        table = table.get("data") or []
    if hasattr(table, "values") and hasattr(table, "columns"):   # pandas
        table = table.values.tolist()
    return [list(r) for r in table if r and str(r[0]).strip()]


def choices_from_tables(dest: dict[str, str], conflicts, kinds, descs, labels, overwrite=()) -> Choices:
    """確認画面の表と配置先の選択から、`Choices` を作る。overwrite は、中身を確認して OK を押したファイル（表では編集できない）。"""
    ch = Choices(dest={k: v for k, v in dest.items() if v}, overwrite=set(overwrite))
    for r in _rows(conflicts):
        if _truthy(r[5]):
            ch.rename[str(r[0])] = str(r[6] or "").strip()
    for r in _rows(kinds):
        value = str(r[2] or "").strip()
        if value:
            ch.kinds[str(r[0])] = value
    for r in _rows(descs):
        ch.desc[str(r[0])] = "import" if _truthy(r[3]) else "existing"
    for r in _rows(labels):
        ch.label[str(r[0])] = _truthy(r[2])
    return ch
