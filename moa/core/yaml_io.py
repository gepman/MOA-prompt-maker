"""YAML の round-trip 読み書き（9.1章）。コメントとキー順を保持し、一時ファイル→置き換えで書く。"""
from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString


def make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.allow_unicode = True
    y.width = 1_000_000  # 長いタグ列を折り返さない
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_roundtrip(path: Path):
    with open(path, encoding="utf-8") as f:
        return make_yaml().load(f)


def literal_text(s: str):
    return LiteralScalarString(s) if "\n" in s else s


def flow_map(values: dict) -> CommentedMap:
    """`{ w: 1536, h: 1024 }` のように1行で書く。"""
    m = CommentedMap(values)
    m.fa.set_flow_style()
    return m


def atomic_create_text(path: Path, text: str) -> None:
    """テキストをそのまま（整形・改行の変換なしで）新規作成する。**既に `path` があれば FileExistsError で、上書きしない**。

    一時ファイルに書いてから `os.link` で本来の名前を付ける（存在すれば失敗する）。`os.link` が使えない環境では、
    存在を確かめてから置き換える。インポート（EDITOR_SPEC 15.7章）が使う。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(str(path))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise
        except OSError:   # ハードリンクを作れないファイルシステム
            if path.exists():
                raise FileExistsError(str(path)) from None
            os.replace(tmp, path)
            return
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_replace_text(path: Path, text: str) -> None:
    """既にある `path` を、テキストそのまま（整形・改行の変換なし）で置き換える。一時ファイルに書いてから置き換える。

    `path` が無ければ FileNotFoundError（新規作成は `atomic_create_text`）。インポートの「上書き」（EDITOR_SPEC 15.6章）が使う。
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_dump(doc, path: Path) -> None:
    """`<name>.yaml.tmp` に書いてから置き換える。"""
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        make_yaml().dump(doc, f)
    os.replace(tmp, path)
