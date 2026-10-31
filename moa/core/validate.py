"""意味チェック（9.2章）とプレイスホルダーの整合（5.1章）。

スキーマ違反（9.1章）は repository が先に弾く。ここでは参照・整合・警告を扱う。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .bbox import check_inversion, check_overflow
from .constants import ANY_BRACES_RE, NONE, PLACEHOLDER_RE
from .models import Action, Background, Character, Issue, Scene
from .people import strip_template_count_tags
from .resolve import resolve_outfit

if TYPE_CHECKING:
    from .repository import Repository


def scan_placeholders(text: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """(スロット名, 接尾辞: None / "outfit" / "bodyHair") の一覧と、書式不正な `{{...}}` の一覧を返す。"""
    found = [(m.group(1), m.group(2)) for m in PLACEHOLDER_RE.finditer(text)]
    malformed = [m.group(0) for m in ANY_BRACES_RE.finditer(text)
                 if not PLACEHOLDER_RE.fullmatch(m.group(0))]
    return found, malformed


def action_templates(action: Action) -> list[tuple[str, str]]:
    """Action でプレイスホルダーを使える場所（mossCaption、mossItems[].caption、anima）。"""
    return ([("mossCaption", action.moss_caption)]
            + [(f"mossItems[{i}].caption", it.caption) for i, it in enumerate(action.moss_items, 1)]
            + [("anima", action.anima)])


def _template_tag_warnings(who: str, label: str, text: str, path) -> list[Issue]:
    _, removed = strip_template_count_tags(text)
    return [Issue("warning", f"{who} の {label} に人数タグ/solo '{tag}' があります（コンパイル時に除去します）", path, label)
            for tag in removed]


# ---- 個別のエンティティ ----
def validate_character(repo: "Repository", char: Character) -> list[Issue]:
    path = repo.path_of("character", char.id)
    d = char.default_outfit
    if d and d != NONE and repo.outfit(d) is None:
        return [Issue("error", f"defaultOutfit '{d}' が存在しません", path, "defaultOutfit")]
    return []


def validate_background(repo: "Repository", bg: Background) -> list[Issue]:
    """Background ではプレイスホルダーが一切使えない（mossCaption、mossItems、anima のどこにも）。"""
    path = repo.path_of("background", bg.id)
    who = f"Background '{bg.id}'"
    texts = ([("mossCaption", bg.moss_caption)]
             + [(f"mossItems[{i}].caption", it.caption) for i, it in enumerate(bg.moss_items, 1)]
             + [("anima", bg.anima)])
    issues: list[Issue] = []
    for label, text in texts:
        found, malformed = scan_placeholders(text)
        for slot, suffix in found:
            ph = "{{" + slot + (f".{suffix}" if suffix else "") + "}}"
            issues.append(Issue("error", f"{who} の {label} にプレイスホルダー {ph} があります（Background では使えません）", path, label))
        for bad in malformed:
            issues.append(Issue("error", f"{who} の {label} にプレイスホルダー {bad} があります（Background では使えません）", path, label))
    issues += _template_tag_warnings(who, "anima", bg.anima, path)
    issues += check_inversion(bg.moss_items, who, path)
    issues += check_overflow(bg.moss_items, bg.canvas, who, path)  # Background の items は自分の canvas で判定する
    return issues


def validate_action(repo: "Repository", action: Action) -> list[Issue]:
    path = repo.path_of("action", action.id)
    who = f"Action '{action.id}'"
    issues: list[Issue] = []

    used: set[str] = set()
    for label, text in action_templates(action):
        found, malformed = scan_placeholders(text)
        for bad in malformed:
            issues.append(Issue("error", f"{who} の {label} に不正なプレイスホルダー {bad} があります", path, label))
        for slot, _ in found:
            used.add(slot)
            if slot not in action.slots:
                issues.append(Issue("error", f"{who} の {label} で宣言されていないスロット '{slot}' が使われています", path, label))
    for slot in action.slots:
        if slot not in used:
            issues.append(Issue("warning", f"{who} のスロット '{slot}' は宣言されていますが、"
                                           "mossCaption / mossItems / anima のどこにも使われていません", path, "slots"))

    for slot, value in action.outfits.items():
        if slot not in action.slots:
            issues.append(Issue("error", f"{who} の outfits に宣言されていないスロット '{slot}' があります", path, f"outfits.{slot}"))
        if value != NONE and repo.outfit(value) is None:
            issues.append(Issue("error", f"{who} の outfits[{slot}] の服装 '{value}' が存在しません", path, f"outfits.{slot}"))

    if action.background and repo.background(action.background) is None:
        issues.append(Issue("warning", f"{who} の推奨背景 '{action.background}' が存在しません", path, "background"))

    issues += _template_tag_warnings(who, "anima", action.anima, path)
    issues += check_inversion(action.moss_items, who, path)
    return issues


def validate_scene(repo: "Repository", scene: Scene) -> list[Issue]:
    """Scene の参照とキャストの整合。"""
    path = repo.path_of("scene", scene.id)
    issues: list[Issue] = []
    if repo.background(scene.background) is None:
        issues.append(Issue("error", f"Background '{scene.background}' が存在しません", path))
    action = repo.action(scene.action)
    if action is None:
        issues.append(Issue("error", f"Action '{scene.action}' が存在しません", path))
        return issues

    for slot in scene.cast:
        if slot not in action.slots:
            issues.append(Issue("error", f"cast に宣言されていないスロット '{slot}' があります", path))
    for slot in action.slots:
        if slot not in scene.cast:
            issues.append(Issue("error", f"スロット '{slot}' にキャラクターが割り当てられていません", path))

    for slot, entry in scene.cast.items():
        char = repo.character(entry.character)
        if char is None:
            issues.append(Issue("error", f"スロット '{slot}' のキャラクター '{entry.character}' が存在しません", path))
            continue
        if entry.outfit not in (None, NONE) and repo.outfit(entry.outfit) is None:
            issues.append(Issue("error", f"スロット '{slot}' の服装 '{entry.outfit}' が存在しません", path))
            continue
        res = resolve_outfit(slot, entry.outfit, action.outfits, char.default_outfit)
        if res.outfit_id and repo.outfit(res.outfit_id) is None:
            issues.append(Issue("error", f"スロット '{slot}' の服装 '{res.outfit_id}' が存在しません", path))
    return issues


def scene_overflow(repo: "Repository", scene: Scene) -> list[Issue]:
    """Action の items を、Scene の Background の canvas で判定する（Action 単体では canvas が決まらない）。"""
    bg, action = repo.background(scene.background), repo.action(scene.action)
    if bg is None or action is None:
        return []
    return check_overflow(action.moss_items, bg.canvas, f"Action '{action.id}'", repo.path_of("action", action.id))


def scene_issues(repo: "Repository", scene: Scene) -> list[Issue]:
    """Scene をコンパイルするときに確認する一式（Background・Action・Scene の検証と、bbox のはみ出し）。"""
    issues: list[Issue] = []
    bg, action = repo.background(scene.background), repo.action(scene.action)
    if bg is not None:
        issues += validate_background(repo, bg)
    if action is not None:
        issues += validate_action(repo, action)
    return issues + validate_scene(repo, scene) + scene_overflow(repo, scene)


def validate_repo(repo: "Repository") -> list[Issue]:
    """読み込み時の、リポジトリ全体の参照・整合チェック。"""
    issues: list[Issue] = []
    for cid in repo.ids("character"):
        issues += validate_character(repo, repo.character(cid))
    for bid in repo.ids("background"):
        issues += validate_background(repo, repo.background(bid))
    for aid in repo.ids("action"):
        issues += validate_action(repo, repo.action(aid))
    for sid in repo.ids("scene"):
        scene = repo.scene(sid)
        issues += validate_scene(repo, scene) + scene_overflow(repo, scene)
    return issues
