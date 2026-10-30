"""人数タグと solo（7.4章）。"""
from __future__ import annotations

from .constants import COUNT_TAG_RE
from .textutil import collapse_ws


def count_tag(n: int, word: str) -> str:
    """1人は `1girl`、2〜5人は `2girls`〜`5girls`、6人以上は `6+girls`。"""
    if n == 1:
        return f"1{word}"
    if n <= 5:
        return f"{n}{word}s"
    return f"6+{word}s"


def people_tags(girls: int, boys: int, others: int = 0) -> list[str]:
    """人数タグの並び（girls → boys）。girls＋boys＋others がちょうど1人のときだけ `solo` を付ける。

    `others`（性別のタグを付けない人物）は人数タグを作らず、solo の判定にだけ数える。
    0人の性別は出さない。
    """
    tags = []
    if girls:
        tags.append(count_tag(girls, "girl"))
    if boys:
        tags.append(count_tag(boys, "boy"))
    if girls + boys + others == 1:
        tags.append("solo")
    return tags


def is_template_count_tag(tag: str) -> bool:
    """テンプレート側に書かれた人数タグ（`3girls`、`1boy`、`6+girls` など）または単独の `solo`。"""
    t = collapse_ws(tag)
    return bool(COUNT_TAG_RE.match(t)) or t.casefold() == "solo"


def strip_template_count_tags(template: str) -> tuple[str, list[str]]:
    """カンマ区切りの各要素のうち、人数タグと単独の `solo` を除いたテンプレートと、除いた要素の一覧を返す。

    プレイスホルダー展開の**前**に行う（キャラ・服装・体毛ブロックの中身は対象外）。
    `3children` のような girl/boy 以外の人数表現は対象外。ファイル自体は書き換えない。
    """
    kept: list[str] = []
    removed: list[str] = []
    for part in template.split(","):
        if is_template_count_tag(part):
            removed.append(collapse_ws(part))
        else:
            kept.append(part)
    return ",".join(kept), removed
