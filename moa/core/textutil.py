import re
from typing import Iterable


def strip_block(s: str | None) -> str:
    """YAMLの `|` ブロックの末尾改行などを除去する（5章）。"""
    return (s or "").strip()


def collapse_ws(s: str) -> str:
    """連続する空白（改行含む）を1つにして前後を落とす。"""
    return re.sub(r"\s+", " ", s).strip()


def split_tags(s: str | None) -> list[str]:
    """カンマで分割し、空白を整え、空の要素を捨てる。重複は除去しない。"""
    if not s:
        return []
    return [t for t in (collapse_ws(p) for p in s.split(",")) if t]


def clean_tags(s: str | None) -> str:
    """空タグと連続カンマだけを除去して再結合する（重複は残す）。"""
    return ", ".join(split_tags(s))


def dedupe_tags(tags: Iterable[str]) -> list[str]:
    """大文字小文字を区別せず、最初に出現したものを残す（6章）。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        key = t.casefold()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def join_negative(parts: Iterable[str | None]) -> str:
    tags: list[str] = []
    for p in parts:
        tags.extend(split_tags(p))
    return ", ".join(dedupe_tags(tags))


def natural_key(s: str) -> list:
    """数字を数として読む自然順ソート用キー。"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.casefold())]
