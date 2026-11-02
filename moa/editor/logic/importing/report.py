"""結果の表示（EDITOR_SPEC 15.11章）と、確認画面の表（競合・種類・description・表示名）の組み立て／読み取り。

画面は部品の個数を変えない方針なので、可変個の一覧は1つの表（Dataframe）に入れる。ここは Gradio に依存しない。
"""
from __future__ import annotations

from moa.core.repository import Repository

from .plan import (CREATE, F_CREATE, F_MERGE, F_REJECT, F_SKIP_LABEL, F_SKIP_SAME, REJECT, RENAMED, SKIP_CHOICE,
                   SKIP_SAME, SKIP_UNSELECTED, Choices, Plan)

RESULT_HEADERS = ["ファイル", "結果", "詳細"]
CONFLICT_HEADERS = ["ファイル", "種類", "id", "既存のファイル", "別IDで取り込む", "新しいID"]
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
    lines = [f"**{now}：{count(CREATE, RENAMED)} 件**（別ID {count(RENAMED)} 件）＋フォルダ設定 {fcount(F_CREATE, F_MERGE)} 件"
             f" ／ スキップ {count(SKIP_SAME, SKIP_CHOICE, SKIP_UNSELECTED) + fcount(F_SKIP_SAME, F_SKIP_LABEL)} 件"
             f"（同じ内容 {count(SKIP_SAME) + fcount(F_SKIP_SAME)}・選択 {count(SKIP_CHOICE) + fcount(F_SKIP_LABEL)}"
             f"・種類が未選択 {count(SKIP_UNSELECTED)}）"
             f" ／ 却下 {count(REJECT) + fcount(F_REJECT)} 件 ／ 無視 {len(plan.ignored)} 件 ／ 警告 {warns} 件"]
    lines += [f"- {n}" for n in plan.notes]
    return "\n".join(lines)


# ---- 確認画面の表 ----
def conflict_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[i.path, i.kind, i.id, i.existing, bool((choices.rename.get(i.path) or "").strip()),
             (choices.rename.get(i.path) or "").strip() or i.suggested] for i in plan.items if i.conflict]


def kinds_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[i.path, " / ".join(i.candidates), choices.kinds.get(i.path, "")] for i in plan.items if i.candidates and not i.kind
            and i.action != REJECT]


def desc_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[f.key, f.desc_conflict[0], f.desc_conflict[1], choices.desc.get(f.key) == "import"]
            for f in plan.folders if f.desc_conflict]


def label_rows(plan: Plan, choices: Choices) -> list[list]:
    return [[f.key, f.import_name or "（name なし）", bool(choices.label.get(f.key))] for f in plan.folders if f.label_choice]


def _rows(table) -> list[list]:
    """Dataframe（type="array"）の値から、中身のある行だけを取り出す。"""
    if table is None:
        return []
    if isinstance(table, dict):   # Gradio が持つ表の値（{"headers": …, "data": [[…]]}）
        table = table.get("data") or []
    if hasattr(table, "values") and hasattr(table, "columns"):   # pandas
        table = table.values.tolist()
    return [list(r) for r in table if r and str(r[0]).strip()]


def choices_from_tables(dest: dict[str, str], conflicts, kinds, descs, labels) -> Choices:
    """確認画面の表と配置先の選択から、`Choices` を作る。"""
    ch = Choices(dest={k: v for k, v in dest.items() if v})
    for r in _rows(conflicts):
        if _truthy(r[4]):
            ch.rename[str(r[0])] = str(r[5] or "").strip()
    for r in _rows(kinds):
        value = str(r[2] or "").strip()
        if value:
            ch.kinds[str(r[0])] = value
    for r in _rows(descs):
        ch.desc[str(r[0])] = "import" if _truthy(r[3]) else "existing"
    for r in _rows(labels):
        ch.label[str(r[0])] = _truthy(r[2])
    return ch
