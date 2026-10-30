"""v1.5 のコンパイルの最小限の再実装。移行レポートの「移行前」の出力を作るためだけに使う。

旧 core は v2 への移行で削除したため、ここに必要な分だけ持つ。`spec/samples/data` を通して
`spec_v2/samples/examples/v1.5/` と一致することをテストで確認している（tests/test_migrate.py）。
入力は YAML をそのまま読んだ dict（スキーマ検証はしない）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from moa.core.constants import BASE_NEGATIVE_ANIMA, BASE_NEGATIVE_MOSS, PLACEHOLDER_RE
from moa.core.textutil import clean_tags, collapse_ws, join_negative, strip_block


@dataclass
class V1Result:
    moss: str
    anima: str
    negative_moss: str
    negative_anima: str


def _unspecified(value) -> bool:
    """v1.x では、キーなし・null・空文字はいずれも「未指定」。"""
    return value is None or not str(value).strip()


def _resolve_outfit(slot: str, scene: dict, sit: dict, char: dict) -> str | None:
    for layer in (scene.get("outfits") or {}, sit.get("outfits") or {}):
        value = layer.get(slot)
        if _unspecified(value):
            continue
        return None if str(value).strip() == "none" else str(value).strip()
    default = char.get("defaultOutfit")
    if _unspecified(default) or str(default).strip() == "none":
        return None
    return str(default).strip()


def _substitute(text: str, blocks: dict[str, dict[str, str]]) -> str:
    def repl(m):
        return blocks.get(m.group(1), {}).get(m.group(2) or "character", "")
    return PLACEHOLDER_RE.sub(repl, text)


def _walk(node, blocks):
    if isinstance(node, str):
        return collapse_ws(_substitute(node, blocks)) if PLACEHOLDER_RE.search(node) else node
    if isinstance(node, list):
        return [_walk(v, blocks) for v in node]
    if isinstance(node, dict):
        return {k: _walk(v, blocks) for k, v in node.items()}
    return node


def compile_v1(chars: dict[str, dict], outfits: dict[str, dict], sit: dict, scene: dict) -> V1Result:
    """失敗（参照先なし、スロットの不足など）は ValueError。"""
    slots = list(sit.get("slots") or [])
    cast = scene.get("cast") or {}
    missing = [s for s in slots if s not in cast]
    if missing:
        raise ValueError(f"スロット {', '.join(missing)} にキャラクターが割り当てられていません")

    moss_blocks, anima_blocks, used_chars, used_outfits = {}, {}, [], []
    for slot in slots:
        char = chars.get(cast[slot])
        if char is None:
            raise ValueError(f"キャラクター '{cast[slot]}' が見つかりません")
        oid = _resolve_outfit(slot, scene, sit, char)
        outfit = outfits.get(oid) if oid else None
        if oid and outfit is None:
            raise ValueError(f"服装 '{oid}' が見つかりません")
        moss_blocks[slot] = {"character": strip_block(char.get("moss")),
                             "outfit": strip_block(outfit.get("moss")) if outfit else "",
                             "bodyHair": strip_block(char.get("bodyHairMoss"))}
        anima_blocks[slot] = {"character": strip_block(char.get("anima")),
                              "outfit": strip_block(outfit.get("anima")) if outfit else "",
                              "bodyHair": strip_block(char.get("bodyHairAnima"))}
        used_chars.append(char)
        if outfit:
            used_outfits.append(outfit)

    tree = json.loads(sit["mossPrompt"])
    return V1Result(
        moss=json.dumps(_walk(tree, moss_blocks), indent=2, ensure_ascii=False),
        anima=clean_tags(_substitute(sit["animaPrompt"], anima_blocks)),
        negative_moss=join_negative([BASE_NEGATIVE_MOSS] + [c.get("negativeMoss") for c in used_chars]
                                    + [o.get("negativeMoss") for o in used_outfits] + [sit.get("negativeMoss")]),
        negative_anima=join_negative([BASE_NEGATIVE_ANIMA] + [c.get("negativeAnima") for c in used_chars]
                                     + [o.get("negativeAnima") for o in used_outfits] + [sit.get("negativeAnima")]),
    )
