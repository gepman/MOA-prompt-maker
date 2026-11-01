"""Scene → MOSS JSON / Animaタグ / ネガティブ（7章）。"""
from __future__ import annotations

import json

from .constants import (BASE_NEGATIVE_ANIMA, BASE_NEGATIVE_MOSS, META_BASE, MOSS_BBOX_FORMAT, MOSS_CANVAS,
                        MOSS_COORD_SPACE, PLACEHOLDER_RE, QUALITY_TAGS, RATINGS)
from .models import CompileResult, MossItem, Scene, SlotResolution, has_error
from .people import people_tags, strip_template_count_tags
from .repository import Repository
from .resolve import resolve_outfit
from .sourcemap import Origin, build_sources, gen as gen_origin
from .textutil import clean_tags, collapse_ws, join_negative, strip_block
from .validate import scene_issues


def _substitute(text: str, blocks: dict[str, dict[str, str]]) -> str:
    """blocks: slot -> {"character": ..., "outfit": ..., "bodyHair": ...}。

    接尾辞なしはキャラ、`.outfit` は服装、`.bodyHair` は体毛。無いものは空文字になる
    （空白・空タグの後始末は、呼び出し側の collapse_ws / clean_tags が行う）。
    """
    def repl(m):
        return blocks.get(m.group(1), {}).get(m.group(2) or "character", "")
    return PLACEHOLDER_RE.sub(repl, text)


def effective_rating(*ratings: str) -> str:
    """厳しい方を採用する（safe < sensitive < explicit）。"""
    return max(ratings, key=RATINGS.index)


def build_meta(rating: str, *tag_lists: list[str]) -> list[str]:
    """`["anime illustration", rating] + Background.metaTags + Action.metaTags`。重複は先勝ち（完全一致）。"""
    merged = [META_BASE, rating]
    for tags in tag_lists:
        merged += tags
    return list(dict.fromkeys(merged))


def _item(it: MossItem, blocks: dict[str, dict[str, str]]) -> dict:
    return {"label": it.label, "caption": collapse_ws(_substitute(it.caption, blocks)), "bbox": list(it.bbox)}


def compile_scene(repo: Repository, scene: Scene) -> CompileResult:
    result = CompileResult(scene_id=scene.id, background_id=scene.background, action_id=scene.action,
                           apply_negative=scene.apply_negative)
    bg, action = repo.background(scene.background), repo.action(scene.action)
    if bg is not None:
        result.canvas = dict(bg.canvas)
    result.issues = scene_issues(repo, scene)
    if has_error(result.issues):  # エラーがあればコンパイル結果を出さない（警告は返す）
        return result

    result.rating = effective_rating(bg.rating, action.rating)

    # 5.2章：服装の解決と、スロットごとのブロック（前後の空白を除去してから使う）
    moss_blocks: dict[str, dict[str, str]] = {}
    anima_blocks: dict[str, dict[str, str]] = {}
    chars, outfits, slot_info = [], [], []
    for slot in action.slots:
        entry = scene.cast[slot]
        char = repo.character(entry.character)
        res = resolve_outfit(slot, entry.outfit, action.outfits, char.default_outfit)
        outfit = repo.outfit(res.outfit_id) if res.outfit_id else None
        moss_blocks[slot] = {"character": strip_block(char.moss),
                             "outfit": strip_block(outfit.moss) if outfit else "",
                             "bodyHair": strip_block(char.body_hair_moss)}
        anima_blocks[slot] = {"character": strip_block(char.anima),
                              "outfit": strip_block(outfit.anima) if outfit else "",
                              "bodyHair": strip_block(char.body_hair_anima)}
        chars.append(char)
        slot_info.append((slot, char, outfit))
        if outfit:
            outfits.append(outfit)
        result.slots.append(SlotResolution(slot, char.id, res.outfit_id, res.source))

    # 7.4章：人数タグと solo。キャストの sex ＋ extras
    girls = sum(c.sex == "female" for c in chars) + action.extras.get("female", 0)
    boys = sum(c.sex == "male" for c in chars) + action.extras.get("male", 0)
    ptags = people_tags(girls, boys, action.extras.get("others", 0))
    result.people_tags = ", ".join(ptags)

    # 7.5章 MOSS：summary.caption は Background → Action の順。片方が空ならもう一方だけ
    captions = [collapse_ws(strip_block(bg.moss_caption)),
                collapse_ws(_substitute(strip_block(action.moss_caption), moss_blocks))]
    moss = {
        "summary": {
            "meta": build_meta(result.rating, bg.meta_tags, action.meta_tags),
            "canvas": MOSS_CANVAS,
            "caption": " ".join(c for c in captions if c),
        },
        "items": [_item(it, {}) for it in bg.moss_items] + [_item(it, moss_blocks) for it in action.moss_items],
        "bbox_coord_space": MOSS_COORD_SPACE,
        "bbox_format": MOSS_BBOX_FORMAT,
    }
    result.moss_prompt = json.dumps(moss, indent=2, ensure_ascii=False)

    # 7.5章 Anima：品質 → rating → 人数 → Action → Background。テンプレートの人数タグは展開前に除去する。
    # 連結してから空タグ・連続カンマだけを除去する（重複タグは残す）
    action_tags, _ = strip_template_count_tags(strip_block(action.anima))
    bg_tags, _ = strip_template_count_tags(strip_block(bg.anima))
    result.anima_prompt = clean_tags(", ".join(
        [QUALITY_TAGS, result.rating, result.people_tags, _substitute(action_tags, anima_blocks), bg_tags]))

    # 6章：全体ベース ＋ キャスト（スロット順）＋ 服装（スロット順）＋ Background ＋ Action
    result.negative_moss = join_negative(
        [BASE_NEGATIVE_MOSS] + [c.negative_moss for c in chars] + [o.negative_moss for o in outfits]
        + [bg.negative_moss, action.negative_moss])
    result.negative_anima = join_negative(
        [BASE_NEGATIVE_ANIMA] + [c.negative_anima for c in chars] + [o.negative_anima for o in outfits]
        + [bg.negative_anima, action.negative_anima])

    # 10.1章：出どころの記録（出力文字列には影響しない。層の並びは上のネガティブ合成と同じ）
    def layers(attr: str, base: str):
        return ([(base, gen_origin("negativeBase"))]
                + [(getattr(c, attr), Origin("character", c.id, "negativeMoss" if attr == "negative_moss" else "negativeAnima")) for c in chars]
                + [(getattr(o, attr), Origin("outfit", o.id, "negativeMoss" if attr == "negative_moss" else "negativeAnima")) for o in outfits]
                + [(getattr(bg, attr), Origin("background", bg.id, "negativeMoss" if attr == "negative_moss" else "negativeAnima")),
                   (getattr(action, attr), Origin("action", action.id, "negativeMoss" if attr == "negative_moss" else "negativeAnima"))])

    result.sources = build_sources(bg, action, slot_info, result.rating, moss["summary"]["meta"], ptags,
                                   layers("negative_moss", BASE_NEGATIVE_MOSS), layers("negative_anima", BASE_NEGATIVE_ANIMA))
    return result
