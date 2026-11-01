"""クイック編集の差分を、色付きの HTML にする（SPEC 10.1章）。

差分の計算は moa.core.quickdiff が作った Row をそのまま使う（ここでは比較しない）。
画面に出す文字列はすべて html.escape する。追加は緑の背景＋太字＋「＋」、削除はグレーの取り消し線＋「−」
（色だけに頼らない）。

field（moss / anima / negm / nega）を渡すと、変更箇所（追加・削除の部分）と行末の［移動］ボタンが
クリックできるようになる。data-moa-field / data-moa-pos に、編集欄の名前とカーソルを置く位置（文字数）を持たせ、
実際の移動はページに入れた JS（moa.ui.style.JUMP_JS）が行う。
"""
from __future__ import annotations

from html import escape

from moa.core.quickdiff import Row

JUMP_TITLE = "クリックで編集欄の該当箇所へ移動"


def _attrs(field: str | None, pos: int | None) -> str:
    if field is None or pos is None:
        return ""
    return f' data-moa-field="{escape(field)}" data-moa-pos="{int(pos)}" title="{JUMP_TITLE}"'


def _del(text: str, field: str | None = None, pos: int | None = None) -> str:
    cls = "moa-del moa-jump" if field is not None and pos is not None else "moa-del"
    return f'<del class="{cls}"{_attrs(field, pos)}>{escape(text)}</del>'


def _ins(text: str, field: str | None = None, pos: int | None = None) -> str:
    cls = "moa-ins moa-jump" if field is not None and pos is not None else "moa-ins"
    return f'<ins class="{cls}"{_attrs(field, pos)}>{escape(text)}</ins>'


def _row(row: Row, field: str | None) -> str:
    note = f' <span class="moa-note">{escape(row.note.strip())}</span>' if row.note else ""
    cls = "moa-row moa-indent" if row.indent else "moa-row"
    if row.kind == "head":
        return f'<div class="moa-head">{escape(row.text)}</div>'
    if row.kind == "del":
        body = f'<span class="moa-mark">−</span>{_del(row.text, field, row.pos)}'
    elif row.kind == "ins":
        body = f'<span class="moa-mark">＋</span>{_ins(row.text, field, row.pos)}'
    elif row.kind == "pair":
        body = f"{_del(row.text, field, row.pos)} → {_ins(row.new, field, row.pos)}"
    else:   # inline：単語差分
        seg_pos = row.seg_pos or [None] * len(row.segs)
        body = "".join(escape(t) if op == "eq" else (_del(t, field, p) if op == "del" else _ins(t, field, p))
                       for (op, t), p in zip(row.segs, seg_pos))
    go = ""
    if field is not None and row.pos is not None:   # 行の［移動］ボタン（その行の最初の変更箇所へ）
        go = f' <button type="button" class="moa-go"{_attrs(field, row.pos)}>移動</button>'
    return f'<div class="{cls}">{body}{note}{go}</div>'


def rows_to_html(rows: list[Row], field: str | None = None) -> str:
    """差分の行を HTML にする。行がなければ空文字（差分表示欄を出さない）。"""
    if not rows:
        return ""
    return '<div class="moa-diff">' + "".join(_row(r, field) for r in rows) + "</div>"
