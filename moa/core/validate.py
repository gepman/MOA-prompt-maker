"""意味チェック（9.2章）とプレイスホルダーの整合（5章）。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Iterator

from .constants import ANY_BRACES_RE, NONE, PLACEHOLDER_RE
from .models import Issue, Scene, Situation
from .resolve import resolve_outfit

if TYPE_CHECKING:
    from .repository import Repository


def collect_strings(node) -> Iterator[str]:
    """JSONの文字列値だけを再帰的に取り出す（キーは対象外）。"""
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for v in node:
            yield from collect_strings(v)
    elif isinstance(node, dict):
        for v in node.values():
            yield from collect_strings(v)


def scan_placeholders(text: str) -> tuple[list[tuple[str, bool]], list[str]]:
    """(スロット名, outfitか) の一覧と、書式不正な `{{...}}` の一覧を返す。"""
    found = [(m.group(1), m.group(2) is not None) for m in PLACEHOLDER_RE.finditer(text)]
    malformed = [m.group(0) for m in ANY_BRACES_RE.finditer(text)
                 if not PLACEHOLDER_RE.fullmatch(m.group(0))]
    return found, malformed


def _check_outfit_layer(repo: "Repository", layer: str, outfits: dict, slots: list[str], path) -> list[Issue]:
    issues: list[Issue] = []
    for slot, value in outfits.items():
        if slot not in slots:
            issues.append(Issue("error", f"{layer}.outfits に宣言されていないスロット '{slot}' があります", path))
        if value and value.strip() != NONE and repo.outfit(value.strip()) is None:
            issues.append(Issue("error", f"{layer}.outfits[{slot}] の服装 '{value}' が存在しません", path))
    return issues


def validate_situation(repo: "Repository", sit: Situation) -> list[Issue]:
    path = repo.path_of("situation", sit.id)
    issues: list[Issue] = []

    for key in ("w", "h"):
        v = sit.canvas.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            issues.append(Issue("error", f"canvas.{key} は正の整数である必要があります", path))

    used: set[str] = set()
    texts: list[tuple[str, str]] = [("animaPrompt", sit.anima_prompt)]
    try:
        moss_tree = json.loads(sit.moss_prompt)
        texts += [("mossPrompt", s) for s in collect_strings(moss_tree)]
    except json.JSONDecodeError as e:
        issues.append(Issue("error", f"mossPrompt がJSONとして不正です: {e}", path))

    for label, text in texts:
        found, malformed = scan_placeholders(text)
        for bad in malformed:
            issues.append(Issue("error", f"{label} に不正なプレイスホルダー {bad} があります", path))
        for slot, _ in found:
            used.add(slot)
            if slot not in sit.slots:
                issues.append(Issue("error", f"{label} で宣言されていないスロット '{slot}' が使われています", path))

    for slot in sit.slots:
        if slot not in used:
            issues.append(Issue("warning", f"スロット '{slot}' は宣言されていますが使われていません", path))

    issues += _check_outfit_layer(repo, "situation", sit.outfits, sit.slots, path)
    return issues


def validate_scene(repo: "Repository", scene: Scene) -> list[Issue]:
    path = repo.path_of("scene", scene.id)
    sit = repo.situation(scene.situation)
    if sit is None:
        return [Issue("error", f"Situation '{scene.situation}' が存在しません", path)]

    issues: list[Issue] = []
    for slot in scene.cast:
        if slot not in sit.slots:
            issues.append(Issue("error", f"cast に宣言されていないスロット '{slot}' があります", path))
    for slot in sit.slots:
        if slot not in scene.cast:
            issues.append(Issue("error", f"スロット '{slot}' にキャラクターが割り当てられていません", path))
    issues += _check_outfit_layer(repo, "scene", scene.outfits, sit.slots, path)

    for slot, cid in scene.cast.items():
        char = repo.character(cid)
        if char is None:
            issues.append(Issue("error", f"スロット '{slot}' のキャラクター '{cid}' が存在しません", path))
            continue
        res = resolve_outfit(slot, scene.outfits, sit.outfits, char.default_outfit)
        if res.outfit_id and repo.outfit(res.outfit_id) is None:
            issues.append(Issue("error", f"スロット '{slot}' の服装 '{res.outfit_id}' が存在しません", path))
    return issues


def validate_repo(repo: "Repository") -> list[Issue]:
    """リポジトリ全体の参照チェック。"""
    issues: list[Issue] = []
    for cid in repo.ids("character"):
        char = repo.character(cid)
        d = (char.default_outfit or "").strip()
        if d and d != NONE and repo.outfit(d) is None:
            issues.append(Issue("error", f"defaultOutfit '{d}' が存在しません", repo.path_of("character", cid)))
    for sid in repo.ids("situation"):
        issues += validate_situation(repo, repo.situation(sid))
    for sid in repo.ids("scene"):
        issues += validate_scene(repo, repo.scene(sid))
    return issues
