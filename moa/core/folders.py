"""フォルダ名の検証と作成（EDITOR_SPEC 2章）。エディタとビルダーで共用する。"""
from __future__ import annotations

import re
from pathlib import Path

_FORBIDDEN = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def check_folder_name(name: str) -> str:
    """名前そのものの規則だけを確かめる（同名の存在は見ない）。問題なければ名前を返す。不可なら ValueError（理由つき）。

    インポート（EDITOR_SPEC 15章）で、既存フォルダへ合流するときにも使う。
    """
    if name is None or name == "" or name in (".", ".."):
        raise ValueError("フォルダ名を入力してください（空、`.`、`..` は使えません）")
    if "/" in name or "\\" in name:
        raise ValueError("フォルダ名にパス区切り（/ \\）は使えません")
    if name.startswith(("_", ".")):
        raise ValueError("フォルダ名の先頭に `_` と `.` は使えません")
    if _FORBIDDEN.search(name):
        raise ValueError('フォルダ名に使えない文字（< > : " | ? * と制御文字）があります')
    if name != name.rstrip(" ."):
        raise ValueError("フォルダ名の末尾に空白とピリオドは使えません")
    if name.split(".", 1)[0].upper() in _RESERVED:
        raise ValueError(f"`{name}` は Windows の予約名のため使えません")
    return name


def validate_folder_name(parent: Path, name: str) -> str:
    """問題なければ名前を返す。不可なら ValueError（理由つき）。名前の規則に加えて、同じ場所に同名があれば不可。"""
    check_folder_name(name)
    if (Path(parent) / name).exists():
        raise ValueError(f"同じ場所に `{name}` があります")
    return name


def create_folder(section_root: Path, parent: Path, name: str) -> Path:
    """section_root（4ルートのどれか）の配下にフォルダを作る。`_folder.yaml` は作らない。"""
    section_root, parent = Path(section_root).resolve(), Path(parent).resolve()
    try:
        parent.relative_to(section_root)
    except ValueError:
        raise ValueError(f"作成先は {section_root.name}/ 配下のフォルダを選んでください") from None
    validate_folder_name(parent, name)
    path = parent / name
    path.mkdir()
    return path
