"""移行レポート（MIGRATION_REPORT.md）の組み立て（SPEC v2 の14章）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .convert import MigrationResult, SceneResult, SituationResult, diff_lines, diff_tags


def _tags(tags: list[str]) -> str:
    return ", ".join(f"`{t}`" for t in tags)


def _rel(path: Path | None, base: Path) -> str:
    if path is None:
        return "（なし）"
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _scene_diff(sc: SceneResult) -> list[str]:
    """Scene 1つ分の、移行前後のコンパイル結果の差分。"""
    out = [f"#### Scene `{sc.id}`"]
    out += [f"- {n}" for n in sc.notes]
    if sc.before_error:
        out.append(f"- 移行前（v1.x）のコンパイルができませんでした：{sc.before_error}")
    if sc.after_error:
        out.append(f"- 移行後（v2）のコンパイルができませんでした：{sc.after_error}")
    if sc.before is None or sc.after is None or not sc.after.ok:
        return out + [""]

    b, a = sc.before, sc.after
    differences: list[str] = []
    if b.moss != a.moss_prompt:
        differences += ["- MOSS：差分あり", "", "  ```diff"] + [f"  {line}" for line in diff_lines(b.moss, a.moss_prompt)] + ["  ```"]
    for label, before, after in (("Anima", b.anima, a.anima_prompt), ("ネガティブ（MOSS）", b.negative_moss, a.negative_moss),
                                 ("ネガティブ（Anima）", b.negative_anima, a.negative_anima)):
        if before == after:
            continue
        d = diff_tags(before, after)
        parts = []
        if d["added"]:
            parts.append(f"追加 {_tags(d['added'])}")
        if d["removed"]:
            parts.append(f"除去 {_tags(d['removed'])}")
        if d["reordered"]:
            parts.append("並びだけが違います")
        differences.append(f"- {label}：" + " ／ ".join(parts))
    if differences:
        out += differences
    else:
        out.append("- 移行前後のコンパイル結果：**差分なし**（MOSS・Anima・ネガティブとも完全一致）")
    if a.people_tags:
        out.append(f"- v2 で付く人数タグ：{_tags(a.people_tags.split(', '))}")
    out += [f"- v2 の警告：{i.message}" for i in a.issues if i.level == "warning"]
    return out + [""]


def _situation_section(s: SituationResult, scenes: dict[str, SceneResult], src: Path, out: Path) -> list[str]:
    lines = [f"### {s.name}（`{s.id}`）", "", f"- 元：`{_rel(s.src, src)}`"]
    if s.background_path is None:
        lines += [f"- **移行できませんでした**：{e}" for e in s.errors] + [""]
        return lines
    lines += [f"- 生成：`{_rel(s.background_path, out)}`、`{_rel(s.action_path, out)}`",
              f"- 使われている Scene：{', '.join(f'`{i}`' for i in s.scenes) or '（なし）'}"]
    if s.removed_leading:
        lines.append(f"- anima の先頭から除いたタグ（v2 ではコンパイラが付けます）：{_tags(s.removed_leading)}")

    if s.count_tags_left:
        lines.append(f"- **Action の anima に人数タグ・solo が残っています：{_tags(s.count_tags_left)}**。v2 ではコンパイル時に除去されて警告になり、"
                     "同じ人数タグがキャストから生成されます。**特定のモブを表す人数タグ（例 `1boy`）は、Action に `extras` を手で設定する必要があります**"
                     "（`extras` がないと、キャスト1人の場面に誤って `solo` が付きます）")
    if s.other_count_like:
        warn = ("**キャスト1人の Action なので、`extras.others` を設定しない限り誤って `solo` が付きます**。"
                if len(s.slots) == 1 else "必要なら `extras` を設定してください。")
        lines.append(f"- 人数を表していそうな他のタグ：{_tags(s.other_count_like)}。これらは除去されず、生成される人数タグと併存します。{warn}")
    if not s.had_people_tags:
        lines.append("- v1.x ではこの Situation に人数タグ（`1girl`、`3girls` など）がありませんでした。v2 では Scene のキャストから"
                     "`1girl` などの人数タグが**新しく付きます**（下の Scene ごとの「v2 で付く人数タグ」を参照）")
    lines.append("- summary.caption の背景／行為への書き分けが残っています。機械移行では全文が Action 側に入り、Background の `mossCaption` は空です"
                 "（書き分けは任意で、しなくても動きます）")
    lines += [f"- 固定値のため破棄：{d}" for d in s.discarded]
    lines += [f"- 変換した値：{c}" for c in s.coerced]
    lines += [f"- 移行先に置き場がなく捨てたもの：`{d}`" for d in s.dropped]
    lines += [f"- **注意**：{e}" for e in s.errors]
    lines.append("")
    for sid in s.scenes:
        if sid in scenes:
            lines += _scene_diff(scenes[sid])
    return lines


def render_report(r: MigrationResult) -> str:
    errors = [i for i in r.post_issues if i.level == "error"]
    warnings = [i for i in r.post_issues if i.level == "warning"]
    migrated = [s for s in r.situations if s.background_path is not None]
    scenes = {s.id: s for s in r.scenes}

    lines = ["# v2 移行レポート", "",
             f"- 元（読み取りのみ。変更していません）：`{r.src}`",
             f"- 先：`{r.out}`",
             f"- 実行：{datetime.now().isoformat(timespec='seconds')}", "",
             "## 概要", "",
             f"- characters：{r.characters} 件、outfits：{r.outfits} 件（そのままコピー。`sex` は書き足していないので female 扱い）",
             f"- Situation：{len(r.situations)} 件 → Background {len(migrated)} 件 ＋ Action {len(migrated)} 件"
             + (f"（移行できなかったもの {len(r.situations) - len(migrated)} 件）" if len(migrated) != len(r.situations) else ""),
             f"- Scene：{len(r.scenes)} 件",
             f"- `_folder.yaml`：{len(r.folder_files)} 件（backgrounds / actions / scenes に複製）",
             f"- 移行後の検査：エラー {len(errors)} 件、警告 {len(warnings)} 件", ""]

    todo = []
    for s in migrated:
        if s.count_tags_left:
            todo.append(f"- [ ] **{s.name}**（`{s.id}`）：Action の anima に残った人数タグ {_tags(s.count_tags_left)} を確認する。"
                        "キャストの人数を表すだけなら対応不要（v2 が自動で付けます）。特定のモブを表すなら `extras` を設定する")
        if s.other_count_like:
            todo.append(f"- [ ] **{s.name}**（`{s.id}`）：{_tags(s.other_count_like)} が、特定の人物の人数なら `extras`（`others` など）を設定する"
                        + ("（キャスト1人なので、設定しないと誤って `solo` が付きます）" if len(s.slots) == 1 else ""))
    lines += ["## 手作業が必要なもの", ""]
    lines += todo or ["- 人数タグまわりの手作業は見つかりませんでした。"]
    lines += ["- [ ] （任意）各 Situation の summary.caption を、Background の `mossCaption` と Action の `mossCaption` に書き分ける", ""]

    lines += ["## Situation ごと", ""]
    for s in r.situations:
        lines += _situation_section(s, scenes, r.src, r.out)

    lines += ["## 移行後データの検査（v2 のスキーマ・意味チェック）", ""]
    if not r.post_issues:
        lines += ["エラー・警告はありません。", ""]
    else:
        for level, label in (("error", "エラー"), ("warning", "警告")):
            items = [i for i in r.post_issues if i.level == level]
            if items:
                lines += [f"### {label}（{len(items)} 件）", ""]
                lines += [f"- {i.message}" + (f"（`{_rel(i.path, r.out)}`）" if i.path else "") for i in items] + [""]

    failed = r.problems + [f"Situation `{s.id}`：{e}" for s in r.situations if s.background_path is None for e in s.errors]
    lines += ["## 移行できなかったもの", ""]
    lines += [f"- {p}" for p in failed] or ["なし。"]
    return "\n".join(lines) + "\n"
