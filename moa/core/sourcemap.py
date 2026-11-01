"""ソースマップ（出どころの記録。SPEC 10.1章）。

コンパイル結果の各部分が、どのエンティティのどの項目から来たかを持つ。`compile_scene` の出力文字列は変えず、
同じブロック・テンプレートから「文字ごとに出どころを持つ列」を別に組み立てて、区間やタグごとの出どころにまとめる。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

from .constants import MOSS_BBOX_FORMAT, MOSS_CANVAS, MOSS_COORD_SPACE, PLACEHOLDER_RE, QUALITY_TAGS
from .people import strip_template_count_tags
from .textutil import split_tags, strip_block

KIND_LABEL = {"character": "Character", "outfit": "Outfit", "background": "Background", "action": "Action"}


@dataclass(frozen=True)
class Origin:
    kind: str                # character | outfit | background | action | generated
    id: str | None
    path: str                # 項目のパス（例 mossItems[1].caption / anima）。generated では種別（rating など）
    via: str | None = None   # プレイスホルダーで展開された部分は、その記法（例 {{center}}）

    @property
    def generated(self) -> bool:
        return self.kind == "generated"

    def label(self) -> str:
        if self.generated:
            return f"生成（{self.path}）"
        text = f"{KIND_LABEL[self.kind]} {self.id} の {self.path}"
        return text + (f"（{self.via}）" if self.via else "")


def gen(path: str) -> Origin:
    return Origin("generated", None, path)


Span = tuple[int, int, Origin]       # 文字列フィールド内の [start, end) の区間
Chars = list[tuple[str, Origin]]


@dataclass
class SourceMap:
    moss: dict[tuple, list[Span]] = field(default_factory=dict)   # JSON のパス → 区間（文字列以外は1区間）
    anima: list[Origin] = field(default_factory=list)             # Anima のタグごと（並び順）
    negative_moss: list[Origin] = field(default_factory=list)
    negative_anima: list[Origin] = field(default_factory=list)

    def moss_origin(self, path: tuple, start: int | None = None, end: int | None = None) -> Origin | None:
        """パスの区間のうち [start, end) に重なる最初のもの（範囲なしならパスの先頭の区間）。"""
        spans = self.moss.get(path)
        if not spans:
            return None
        if start is None:
            return spans[0][2]
        for s, e, o in spans:
            if s < max(end, start + 1) and start < e:
                return o
        # 挿入位置（範囲が空）や末尾：直前の区間、なければ最後の区間
        before = [o for s, e, o in spans if e <= start]
        return before[-1] if before else spans[-1][2]


# ---- 文字ごとの出どころ ----
def tr(text: str, origin: Origin) -> Chars:
    return [(c, origin) for c in text]


def join(parts: Iterable[Chars], sep: Chars) -> Chars:
    out: Chars = []
    for i, p in enumerate(parts):
        if i:
            out += sep
        out += p
    return out


def substitute(text: str, origin: Origin, blocks: dict[str, dict[str, tuple[str, Origin]]]) -> Chars:
    """compiler._substitute と同じ置換。テンプレート部分は origin、展開した部分はブロックの出どころ（via 付き）。"""
    out: Chars = []
    pos = 0
    for m in PLACEHOLDER_RE.finditer(text):
        out += tr(text[pos:m.start()], origin)
        blk = blocks.get(m.group(1), {}).get(m.group(2) or "character")
        if blk and blk[0]:
            out += tr(blk[0], replace(blk[1], via=m.group(0)))
        pos = m.end()
    out += tr(text[pos:], origin)
    return out


def collapse(chars: Chars) -> Chars:
    """textutil.collapse_ws と同じ整形（連続する空白を1つにして前後を落とす）。空白は最初の空白文字の持ち主にする。"""
    out: Chars = []
    for ch, o in chars:
        if ch.isspace():
            if not out or out[-1][0] == " ":
                continue
            out.append((" ", o))
        else:
            out.append((ch, o))
    if out and out[-1][0] == " ":
        out.pop()
    return out


def text_of(chars: Chars) -> str:
    return "".join(c for c, _ in chars)


def split_tags_tracked(chars: Chars) -> list[Chars]:
    """textutil.split_tags と同じ分割・整形・空要素の除去。"""
    parts: list[Chars] = [[]]
    for item in chars:
        if item[0] == ",":
            parts.append([])
        else:
            parts[-1].append(item)
    return [p for p in (collapse(p) for p in parts) if p]


def spans_of(chars: Chars) -> list[Span]:
    spans: list[Span] = []
    for i, (_, o) in enumerate(chars):
        if spans and spans[-1][2] == o and spans[-1][1] == i:
            spans[-1] = (spans[-1][0], i + 1, o)
        else:
            spans.append((i, i + 1, o))
    return spans


# ---- 組み立て ----
def _block(text: str, origin: Origin) -> tuple[str, Origin]:
    return strip_block(text), origin


def build_sources(bg, action, slot_info, rating: str, meta_tags: list[str], people: list[str],
                  negative_layers_moss: list[tuple[str, Origin]],
                  negative_layers_anima: list[tuple[str, Origin]]) -> SourceMap:
    """slot_info: [(slot, Character, Outfit | None)]（Action.slots の順）。"""
    sm = SourceMap()
    bg_o = lambda p: Origin("background", bg.id, p)       # noqa: E731
    ac_o = lambda p: Origin("action", action.id, p)       # noqa: E731

    moss_blocks: dict[str, dict[str, tuple[str, Origin]]] = {}
    anima_blocks: dict[str, dict[str, tuple[str, Origin]]] = {}
    for slot, char, outfit in slot_info:
        moss_blocks[slot] = {
            "character": _block(char.moss, Origin("character", char.id, "moss")),
            "outfit": _block(outfit.moss, Origin("outfit", outfit.id, "moss")) if outfit else ("", gen("none")),
            "bodyHair": _block(char.body_hair_moss, Origin("character", char.id, "bodyHairMoss"))}
        anima_blocks[slot] = {
            "character": _block(char.anima, Origin("character", char.id, "anima")),
            "outfit": _block(outfit.anima, Origin("outfit", outfit.id, "anima")) if outfit else ("", gen("none")),
            "bodyHair": _block(char.body_hair_anima, Origin("character", char.id, "bodyHairAnima"))}

    # ---- MOSS ----
    meta: list[tuple[str, Origin]] = [("anime illustration", gen("meta")), (rating, gen("rating"))]
    meta += [(t, bg_o("metaTags")) for t in bg.meta_tags] + [(t, ac_o("metaTags")) for t in action.meta_tags]
    seen: set[str] = set()
    n = 0
    for text, o in meta:
        if text not in seen:      # build_meta と同じく完全一致の先勝ち
            seen.add(text)
            sm.moss[("summary", "meta", n)] = [(0, len(text), o)]
            n += 1
    sm.moss[("summary", "canvas")] = [(0, len(MOSS_CANVAS), gen("canvas"))]
    sm.moss[("bbox_coord_space",)] = [(0, len(MOSS_COORD_SPACE), gen("bbox_coord_space"))]
    sm.moss[("bbox_format",)] = [(0, len(MOSS_BBOX_FORMAT), gen("bbox_format"))]

    bg_cap = collapse(tr(strip_block(bg.moss_caption), bg_o("mossCaption")))
    ac_cap = collapse(substitute(strip_block(action.moss_caption), ac_o("mossCaption"), moss_blocks))
    caption = join([c for c in (bg_cap, ac_cap) if c], tr(" ", gen("caption")))
    sm.moss[("summary", "caption")] = spans_of(caption)

    idx = 0
    for owner, items, blocks in ((bg_o, bg.moss_items, {}), (ac_o, action.moss_items, moss_blocks)):
        for j, it in enumerate(items):
            sm.moss[("items", idx, "label")] = [(0, len(it.label), owner(f"mossItems[{j}].label"))]
            cap = collapse(substitute(it.caption, owner(f"mossItems[{j}].caption"), blocks))
            sm.moss[("items", idx, "caption")] = spans_of(cap)
            sm.moss[("items", idx, "bbox")] = [(0, 1, owner(f"mossItems[{j}].bbox"))]
            idx += 1

    # ---- Anima：品質 → rating → 人数 → Action → Background ----
    sep = tr(", ", gen("joiner"))
    action_tags, _ = strip_template_count_tags(strip_block(action.anima))
    bg_tags, _ = strip_template_count_tags(strip_block(bg.anima))
    people_chars = join([tr(t, gen("solo" if t == "solo" else "people")) for t in people], sep)
    anima = join([tr(QUALITY_TAGS, gen("quality")), tr(rating, gen("rating")), people_chars,
                  substitute(action_tags, ac_o("anima"), anima_blocks), tr(bg_tags, bg_o("anima"))], sep)
    sm.anima = [t[0][1] for t in split_tags_tracked(anima)]

    sm.negative_moss = _negative_origins(negative_layers_moss)
    sm.negative_anima = _negative_origins(negative_layers_anima)
    return sm


def _negative_origins(layers: list[tuple[str, Origin]]) -> list[Origin]:
    """join_negative と同じ並び・重複除去（大文字小文字を区別せず先勝ち）で、タグごとの出どころを返す。"""
    seen: set[str] = set()
    out: list[Origin] = []
    for text, origin in layers:
        for t in split_tags(text):
            if t.casefold() not in seen:
                seen.add(t.casefold())
                out.append(origin)
    return out


def line_paths(moss: dict) -> list[tuple | None]:
    """json.dumps(moss, indent=2) の各行に対応する JSON のパス（行単位の差分で出どころを引くため）。"""
    out: list[tuple | None] = []

    def walk(value, path, key_line: bool):
        # key_line: 親が dict で、キーと同じ行に開き括弧が来る場合も1行として数える
        if isinstance(value, dict) and value:
            out.append(path)                       # "{"（またはキー: {）
            for k, v in value.items():
                walk(v, path + (k,), True)
            out.append(path)                       # "}"
        elif isinstance(value, list) and value:
            out.append(path)
            for i, v in enumerate(value):
                walk(v, path + (i,), False)
            out.append(path)
        else:
            out.append(path)                       # スカラー、空の {} / []

    walk(moss, (), False)
    return out
