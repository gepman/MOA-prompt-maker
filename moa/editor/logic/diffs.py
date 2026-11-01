"""エディタの差分表示（EDITOR_SPEC 14章）。Gradio に依存しない。

差分の比較そのものは、ビルダーのクイック編集と同じ共通部品（moa.core.diffcore：opcodes / tag_lines / word_diff / Row）を使う。
ここは、フォーム値を項目ごとに共通部品へ渡して Row にまとめ、色付き HTML（moa.ui.diffhtml）にするだけで、比較の処理は持たない。

基準（base）は「読み込んだときの内容」（session.baseline）。新しい値（new）はフォームの今の値。どちらも forms.normalize を通して比べる。
"""
from __future__ import annotations

import json
from dataclasses import replace

from moa.core.constants import NONE
from moa.core.diffcore import Row, similar, tag_lines, tag_spans, word_diff
from moa.core.entity_io import ORDER
from moa.core.textutil import split_tags
from moa.ui.diffhtml import rows_to_html
from moa.ui.style import TEXTBOX_ID  # noqa: F401  （編集欄の elem_id の書式。移動先の名前はこれに合わせる）

from . import forms

TAG_KEYS = ("anima", "negativeAnima")                 # タグ単位（並び順）で比べる欄
TEXT_KEYS = ("moss", "mossCaption", "bodyHairMoss", "bodyHairAnima", "negativeMoss")   # 単語単位で比べる欄
LONG_KEYS = ("moss", "anima", "mossCaption", "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima")
LIST_KEYS = ("tags", "metaTags", "slots")             # 要素単位
EMPTY = "（なし）"
INDENT = "  "
SINGLE_KEYS = ("name", "group")                       # 1行の欄（変更点の行から、その欄へ移動できる）
JUMP_LIST_KEYS = ("tags", "metaTags", "slots")


def _lead(text) -> int:
    """編集欄の文字列の先頭にある空白の数（差分は前後の空白を除いた値で比べるので、位置をこの分ずらす）。"""
    return len(text) - len(text.lstrip()) if isinstance(text, str) else 0


def _with_target(rows: list[Row], target: str | None) -> list[Row]:
    return [replace(r, target=target) for r in rows] if target else rows


def _nested(rows: list[Row]) -> list[Row]:
    """見出しの下に入れるので、1段字下げする。"""
    return [replace(r, indent=INDENT + r.indent) for r in rows]


def baseline(st: dict) -> dict | None:
    """差分の基準。複製は複製元の保存済みの内容（st["base"]）、それ以外は開いたとき・保存したときの内容（st["loaded"]）。"""
    return st.get("base") or st.get("loaded")


# ---- 長い文章・タグの欄 ----
def _inline(old: str, new: str, target: str | None, offset: int, indent: str = "") -> Row:
    """単語単位の差分の1行。target があれば、変更箇所（追加は最後尾、削除は消えた箇所）へ移動できる。"""
    segs, _, dec = word_diff(old, new, None, ())
    seg_pos = [None if p is None else p + offset for p in dec] if target else []
    return Row("inline", segs=segs, indent=indent, seg_pos=seg_pos, target=target,
               pos=next((p for p in seg_pos if p is not None), None))


def text_rows(key: str, old: str, new: str, target: str | None = None, offset: int = 0) -> list[Row]:
    """1つの欄の差分の行（見出しなし）。anima / negativeAnima はタグ単位、ほかは単語単位。変更がなければ空。
    target＝移動先の欄の名前、offset＝その欄の文字列の先頭の空白の数（位置のずれ）。省略すると移動はできない。"""
    old, new = old or "", new or ""
    if old == new:
        return []
    if key in TAG_KEYS:
        ends = [e + offset for _, e in tag_spans(new)] if target else None
        rows = tag_lines(split_tags(old), split_tags(new), [], ends, offset if target else None)
        if rows:
            return _with_target(rows, target)
    return [_inline(old, new, target, offset)]   # 単語単位。タグが同じで空白・改行だけが違うときもここに来る


# ---- mossItems のカード ----
def _bbox(b) -> str:
    return "[" + ", ".join(str(int(n)) for n in b) + "]"


def _item_key(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def card_rows(old: dict, new: dict, target: str | None = None, offset: int = 0) -> list[Row]:
    """カード1枚の差分：caption の単語差分、label・bbox の 旧→新（変わったものだけ）。target＝caption 欄の名前（移動先）。"""
    rows: list[Row] = []
    if old["label"] != new["label"]:
        rows += [Row("head", "label"), Row("pair", old["label"] or EMPTY, new["label"] or EMPTY, indent=INDENT)]
    if old["caption"] != new["caption"]:
        rows += [Row("head", "caption"), _inline(old["caption"], new["caption"], target, offset, INDENT)]
    if list(old["bbox"]) != list(new["bbox"]):
        rows += [Row("head", "bbox"), Row("pair", _bbox(old["bbox"]), _bbox(new["bbox"]), indent=INDENT)]
    return rows


def match_items(old: list[dict], new: list[dict]) -> list[int | None]:
    """新しい各カードが、基準のどのカードに当たるか（当たらなければ None）。
    ①内容が同じもの（同じ位置を優先）→ ②caption が似ているもの（label・bbox だけ変えたときも当たる）→ ③同じ位置にある、まだ当たっていない基準のカード。"""
    ko, kn = [_item_key(x) for x in old], [_item_key(x) for x in new]
    match: list[int | None] = [None] * len(new)
    used: set[int] = set()
    for j, k in enumerate(kn):                                   # ① 同じ位置の同じ内容
        if j < len(old) and ko[j] == k:
            match[j] = j
            used.add(j)
    for j, k in enumerate(kn):                                   # ① 別の位置の同じ内容
        if match[j] is None:
            i = next((i for i in range(len(old)) if i not in used and ko[i] == k), None)
            if i is not None:
                match[j] = i
                used.add(i)
    for j, k in enumerate(kn):                                   # ② 似ているもの（近い位置を優先）
        if match[j] is None:
            near = sorted((i for i in range(len(old)) if i not in used), key=lambda i: abs(i - j))
            i = next((i for i in near if similar(old[i]["caption"], new[j]["caption"])), None)
            if i is not None:
                match[j] = i
                used.add(i)
    for j in range(len(kn)):                                     # ③ 同じ位置のまだ当たっていないカード
        if match[j] is None and j < len(old) and j not in used:
            match[j] = j
            used.add(j)
    return match


def _caption_target(raw_items, j: int) -> tuple[str | None, int]:
    """j 番目のカードの caption 欄（移動先の名前）と、その文字列の先頭の空白の数。画面の値（raw_items）があるときだけ移動できる。"""
    if raw_items is None or j >= len(raw_items):
        return None, 0
    return f"c_caption{j}", _lead(raw_items[j].get("caption"))


def card_views(old: list[dict], new: list[dict], raw_items: list[dict] | None = None) -> list[list[Row]]:
    """新しい各カードの下に出す差分（変更がなければ空。基準に当たらないカードは「新しいカード」）。
    raw_items＝画面の今のカード（前後の空白を除く前）。渡すと、caption の変更箇所へ移動できる。"""
    out = []
    for j, i in enumerate(match_items(old, new)):
        target, offset = _caption_target(raw_items, j)
        out.append([Row("ins", "新しく追加したカード")] if i is None else card_rows(old[i], new[j], target, offset))
    return out


def items_rows(old: list[dict], new: list[dict], raw_items: list[dict] | None = None) -> list[Row]:
    """mossItems 全体：カードの追加・削除・並べ替えと、変更したカードの中身。"""
    match = match_items(old, new)
    used = {i for i in match if i is not None}
    rows: list[Row] = []
    for j, i in enumerate(match):
        if i is None:
            rows += [Row("head", f"#{j + 1}（追加）"), Row("ins", json.dumps(new[j], ensure_ascii=False), indent=INDENT)]
            continue
        sub = card_rows(old[i], new[j], *_caption_target(raw_items, j))
        if sub:
            rows += [Row("head", f"#{j + 1}" + (f"（元 #{i + 1}）" if i != j else ""))] + _nested(sub)
    for i in range(len(old)):
        if i not in used:
            rows += [Row("head", f"元の #{i + 1}（削除）"), Row("del", json.dumps(old[i], ensure_ascii=False), indent=INDENT)]
    moved = [(j, i) for j, i in enumerate(match) if i is not None and i != j]
    if moved:
        rows += [Row("head", "並べ替え")] + [Row("pair", f"元 #{i + 1}", f"#{j + 1}", indent=INDENT) for j, i in moved]
    return rows


# ---- 短い項目 ----
def _tri(v: str | None) -> str:
    """服装指定の三値の表示。"""
    return "未指定" if v is None else ("none（着せない）" if v == NONE else v)


def _show(v) -> str:
    return v if isinstance(v, str) and v else EMPTY


def _short(key: str, v) -> str:
    if key == "canvas":
        return f"{v['w']}×{v['h']}"
    if key == "extras":
        return " / ".join(f"{k} {n}" for k, n in sorted(v.items())) or "なし"
    if key == "defaultOutfit":
        return _tri(v)
    if key == "background":
        return v or "（未設定）"
    return _show(v)


def _pair(label: str, old: str, new: str) -> list[Row]:
    return [Row("head", label), Row("pair", old, new, indent=INDENT)]


def short_rows(key: str, old, new, raw=None) -> list[Row]:
    """短い項目 1つの 旧→新（見出し付き）。変更がなければ空。
    raw＝画面の今の文字列（name・group・tags・metaTags・slots の欄）。渡すと、その欄の変更箇所へ移動できる。"""
    if key in LIST_KEYS:   # 要素単位の追加・削除
        jump = isinstance(raw, str) and key in JUMP_LIST_KEYS
        ends = [e for _, e in tag_spans(raw)] if jump else None
        rows = tag_lines(list(old or []), list(new or []), [], ends, 0 if jump else None)
        return ([Row("head", key)] + _nested(_with_target(rows, key if jump else None))) if rows else []
    if key == "outfits":   # スロットごとの三値
        old, new = old or {}, new or {}
        rows: list[Row] = []
        for slot in list(old) + [s for s in new if s not in old]:
            if old.get(slot) != new.get(slot):
                rows += _pair(f"outfits.{slot}", _tri(old.get(slot)), _tri(new.get(slot)))
        return rows
    if old == new:
        return []
    rows = _pair(key, _short(key, old), _short(key, new))
    if key in SINGLE_KEYS and isinstance(raw, str):   # 1行の欄：新しい値の最後尾へ
        rows[1] = replace(rows[1], pos=len(raw.rstrip()), target=key)
    return rows


# ---- 全体 ----
def _normalized(kind: str, values: dict) -> dict:
    return forms.normalize(kind, values)


def change_rows(kind: str, new: dict, base: dict) -> list[Row]:
    """保存前の変更点：変更した全項目を、項目の順（YAML のキー順）に並べる。ID は対象にしない。"""
    n, o = _normalized(kind, new), _normalized(kind, base)
    rows: list[Row] = []
    for key in ORDER[kind]:
        if key == "id" or key not in n:
            continue
        old_v, new_v = o.get(key), n[key]
        raw = new.get(key)   # 画面の今の値（前後の空白を除く前）。変更箇所へ移動する位置の計算に使う
        if key in LONG_KEYS:
            sub = text_rows(key, old_v or "", new_v or "", key if isinstance(raw, str) else None, _lead(raw))
            rows += ([Row("head", key)] + _nested(sub)) if sub else []
        elif key == "mossItems":
            sub = items_rows(old_v or [], new_v, raw)
            rows += ([Row("head", key)] + sub) if sub else []
        else:
            rows += short_rows(key, old_v, new_v, raw)
    return rows


def changes_html(kind: str, new: dict, base: dict) -> str:
    """保存前の変更点（HTML）。変更がなければ空文字。確認欄にも併記する。"""
    return rows_to_html(change_rows(kind, new, base))


def views(kind: str, new: dict, base: dict) -> dict:
    """画面に出す差分（すべて HTML。変更がなければ空文字）：
    fields＝長い欄ごと / cards＝カードごと（新しいカードの枚数分） / changes＝保存前の変更点。"""
    n, o = _normalized(kind, new), _normalized(kind, base)
    fields = {key: rows_to_html(text_rows(key, o.get(key) or "", n[key] or "", key if isinstance(new.get(key), str) else None,
                                          _lead(new.get(key))))
              for key in LONG_KEYS if key in n}
    cards = ([rows_to_html(r) for r in card_views(o.get("mossItems") or [], n["mossItems"], new.get("mossItems"))]
             if "mossItems" in n else [])
    return {"fields": fields, "cards": cards, "changes": rows_to_html(change_rows(kind, new, base))}
