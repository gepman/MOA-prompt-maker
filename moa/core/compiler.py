"""Scene → MOSS JSON / Animaタグ / ネガティブ（7章）。"""
from __future__ import annotations

import json

from .constants import BASE_NEGATIVE_ANIMA, BASE_NEGATIVE_MOSS, PLACEHOLDER_RE
from .models import CompileResult, Issue, Scene, SlotResolution, has_error
from .repository import Repository
from .resolve import resolve_outfit
from .textutil import clean_tags, collapse_ws, join_negative, strip_block
from .validate import validate_scene, validate_situation


def _substitute(text: str, blocks: dict[str, tuple[str, str]]) -> str:
    """blocks: slot -> (キャラのブロック, 服装のブロック)"""
    def repl(m):
        char_block, outfit_block = blocks.get(m.group(1), ("", ""))
        return outfit_block if m.group(2) else char_block
    return PLACEHOLDER_RE.sub(repl, text)


def _walk_moss(node, blocks):
    if isinstance(node, str):
        if PLACEHOLDER_RE.search(node):
            return collapse_ws(_substitute(node, blocks))
        return node
    if isinstance(node, list):
        return [_walk_moss(v, blocks) for v in node]
    if isinstance(node, dict):
        return {k: _walk_moss(v, blocks) for k, v in node.items()}
    return node


def compile_scene(repo: Repository, scene: Scene) -> CompileResult:
    result = CompileResult(scene_id=scene.id, situation_id=scene.situation,
                           apply_negative=scene.apply_negative)
    sit = repo.situation(scene.situation)
    if sit is None:
        result.issues.append(Issue("error", f"Situation '{scene.situation}' が存在しません"))
        return result

    result.canvas = dict(sit.canvas)
    result.issues = validate_situation(repo, sit) + validate_scene(repo, scene)
    if has_error(result.issues):
        return result

    moss_blocks: dict[str, tuple[str, str]] = {}
    anima_blocks: dict[str, tuple[str, str]] = {}
    outfits = []
    chars = []
    for slot in sit.slots:
        char = repo.character(scene.cast[slot])
        res = resolve_outfit(slot, scene.outfits, sit.outfits, char.default_outfit)
        outfit = repo.outfit(res.outfit_id) if res.outfit_id else None
        moss_blocks[slot] = (strip_block(char.moss), strip_block(outfit.moss) if outfit else "")
        anima_blocks[slot] = (strip_block(char.anima), strip_block(outfit.anima) if outfit else "")
        chars.append(char)
        if outfit:
            outfits.append(outfit)
        result.slots.append(SlotResolution(slot, char.id, res.outfit_id, res.source))

    # MOSS: JSONとしてパースしてから、文字列値の中だけを置換する
    tree = _walk_moss(json.loads(sit.moss_prompt), moss_blocks)
    result.moss_prompt = json.dumps(tree, indent=2, ensure_ascii=False)

    # Anima: 文字列置換 → 空タグ・連続カンマのみ除去（重複タグは残す）
    result.anima_prompt = clean_tags(_substitute(sit.anima_prompt, anima_blocks))

    # ネガティブ: 全体ベース + キャラ全員 + 解決された服装 + Situation
    result.negative_moss = join_negative(
        [BASE_NEGATIVE_MOSS] + [c.negative_moss for c in chars]
        + [o.negative_moss for o in outfits] + [sit.negative_moss])
    result.negative_anima = join_negative(
        [BASE_NEGATIVE_ANIMA] + [c.negative_anima for c in chars]
        + [o.negative_anima for o in outfits] + [sit.negative_anima])
    return result
