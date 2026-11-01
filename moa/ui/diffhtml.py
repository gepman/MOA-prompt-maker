"""クイック編集の差分を、色付きの HTML にする（SPEC 10.1章）。

差分の計算は moa.core.quickdiff が作った Row をそのまま使う（ここでは比較しない）。
画面に出す文字列はすべて html.escape する。追加は緑の背景＋太字＋「＋」、削除はグレーの取り消し線＋「−」
（色だけに頼らない）。
"""
from __future__ import annotations

from html import escape

from moa.core.quickdiff import Row


def _del(text: str) -> str:
    return f'<del class="moa-del">{escape(text)}</del>'


def _ins(text: str) -> str:
    return f'<ins class="moa-ins">{escape(text)}</ins>'


def _row(row: Row) -> str:
    note = f' <span class="moa-note">{escape(row.note.strip())}</span>' if row.note else ""
    cls = "moa-row moa-indent" if row.indent else "moa-row"
    if row.kind == "head":
        return f'<div class="moa-head">{escape(row.text)}</div>'
    if row.kind == "del":
        body = f'<span class="moa-mark">−</span>{_del(row.text)}'
    elif row.kind == "ins":
        body = f'<span class="moa-mark">＋</span>{_ins(row.text)}'
    elif row.kind == "pair":
        body = f"{_del(row.text)} → {_ins(row.new)}"
    else:   # inline：単語差分
        body = "".join(escape(t) if op == "eq" else (_del(t) if op == "del" else _ins(t)) for op, t in row.segs)
    return f'<div class="{cls}">{body}{note}</div>'


def rows_to_html(rows: list[Row]) -> str:
    """差分の行を HTML にする。行がなければ空文字（差分表示欄を出さない）。"""
    if not rows:
        return ""
    return '<div class="moa-diff">' + "".join(_row(r) for r in rows) + "</div>"
