"""エディタの差分表示（EDITOR_SPEC 14章）。Gradio に依存しない。

差分の比較そのものは、ビルダーのクイック編集と同じ共通部品（moa.core.diffcore：opcodes / tag_lines / word_diff / Row）を使う。
ここは、フォーム値を項目ごとに共通部品へ渡して Row にまとめ、色付き HTML（moa.ui.diffhtml）にするだけで、比較の処理は持たない。

基準（base）は「読み込んだときの内容」（session.baseline）。新しい値（new）はフォームの今の値。どちらも forms.normalize を通して比べる。
"""
from __future__ import annotations

import json
from dataclasses import replace

from moa.core.constants import NONE
from moa.core.diffcore import Row, similar, tag_lines, word_diff
from moa.core.entity_io import ORDER
from moa.core.textutil import split_tags
from moa.ui.diffhtml import rows_to_html

from . import forms

TAG_KEYS = ("anima", "negativeAnima")                 # タグ単位（並び順）で比べる欄
TEXT_KEYS = ("moss", "mossCaption", "bodyHairMoss", "bodyHairAnima", "negativeMoss")   # 単語単位で比べる欄
LONG_KEYS = ("moss", "anima", "mossCaption", "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima")
LIST_KEYS = ("tags", "metaTags", "slots")             # 要素単位
EMPTY = "（なし）"
INDENT = "  "


def _nested(rows: list[Row]) -> list[Row]:
    """見出しの下に入れるので、1段字下げする。"""
    return [replace(r, indent=INDENT + r.indent) for r in rows]


def baseline(st: dict) -> dict | None:
    """差分の基準。複製は複製元の保存済みの内容（st["base"]）、それ以外は開いたとき・保存したときの内容（st["loaded"]）。"""
    return st.get("base") or st.get("loaded")


# ---- 長い文章・タグの欄 ----
def text_rows(key: str, old: str, new: str) -> list[Row]:
    """1つの欄の差分の行（見出しなし）。anima / negativeAnima はタグ単位、ほかは単語単位。変更がなければ空。"""
    old, new = old or "", new or ""
    if old == new:
        return []
    if key in TAG_KEYS:
        rows = tag_lines(split_tags(old), split_tags(new), [], None, None)
        if rows:
            return rows
    segs, _, _ = word_diff(old, new, None, ())   # 単語単位。タグが同じで空白・改行だけが違うときもここに来る
    return [Row("inline", segs=segs)]


# ---- mossItems のカード ----
def _bbox(b) -> str:
    return "[" + ", ".join(str(int(n)) for n in b) + "]"


def _item_key(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def card_rows(old: dict, new: dict) -> list[Row]:
    """カード1枚の差分：caption の単語差分、label・bbox の 旧→新（変わったものだけ）。"""
    rows: list[Row] = []
    if old["label"] != new["label"]:
        rows += [Row("head", "label"), Row("pair", old["label"] or EMPTY, new["label"] or EMPTY, indent=INDENT)]
    if old["caption"] != new["caption"]:
        segs, _, _ = word_diff(old["caption"], new["caption"], None, ())
        rows += [Row("head", "caption"), Row("inline", segs=segs, indent=INDENT)]
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


def card_views(old: list[dict], new: list[dict]) -> list[list[Row]]:
    """新しい各カードの下に出す差分（変更がなければ空。基準に当たらないカードは「新しいカード」）。"""
    out = []
    for j, i in enumerate(match_items(old, new)):
        out.append([Row("ins", "新しく追加したカード")] if i is None else card_rows(old[i], new[j]))
    return out


def items_rows(old: list[dict], new: list[dict]) -> list[Row]:
    """mossItems 全体：カードの追加・削除・並べ替えと、変更したカードの中身。"""
    match = match_items(old, new)
    used = {i for i in match if i is not None}
    rows: list[Row] = []
    for j, i in enumerate(match):
        if i is None:
            rows += [Row("head", f"#{j + 1}（追加）"), Row("ins", json.dumps(new[j], ensure_ascii=False), indent=INDENT)]
            continue
        sub = card_rows(old[i], new[j])
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


def short_rows(key: str, old, new) -> list[Row]:
    """短い項目 1つの 旧→新（見出し付き）。変更がなければ空。"""
    if key in LIST_KEYS:   # 要素単位の追加・削除
        rows = tag_lines(list(old or []), list(new or []), [], None, None)
        return ([Row("head", key)] + _nested(rows)) if rows else []
    if key == "outfits":   # スロットごとの三値
        old, new = old or {}, new or {}
        rows: list[Row] = []
        for slot in list(old) + [s for s in new if s not in old]:
            if old.get(slot) != new.get(slot):
                rows += _pair(f"outfits.{slot}", _tri(old.get(slot)), _tri(new.get(slot)))
        return rows
    if old == new:
        return []
    return _pair(key, _short(key, old), _short(key, new))


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
        if key in LONG_KEYS:
            sub = text_rows(key, old_v or "", new_v or "")
            rows += ([Row("head", key)] + _nested(sub)) if sub else []
        elif key == "mossItems":
            sub = items_rows(old_v or [], new_v)
            rows += ([Row("head", key)] + sub) if sub else []
        else:
            rows += short_rows(key, old_v, new_v)
    return rows


def changes_html(kind: str, new: dict, base: dict) -> str:
    """保存前の変更点（HTML）。変更がなければ空文字。確認欄にも併記する。"""
    return rows_to_html(change_rows(kind, new, base))


def views(kind: str, new: dict, base: dict) -> dict:
    """画面に出す差分（すべて HTML。変更がなければ空文字）：
    fields＝長い欄ごと / cards＝カードごと（新しいカードの枚数分） / changes＝保存前の変更点。"""
    n, o = _normalized(kind, new), _normalized(kind, base)
    fields = {key: rows_to_html(text_rows(key, o.get(key) or "", n[key] or "")) for key in LONG_KEYS if key in n}
    cards = [rows_to_html(r) for r in card_views(o.get("mossItems") or [], n["mossItems"])] if "mossItems" in n else []
    return {"fields": fields, "cards": cards, "changes": rows_to_html(change_rows(kind, new, base))}
