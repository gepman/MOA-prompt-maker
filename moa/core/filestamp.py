"""外部変更の検知（EDITOR_SPEC 1章）。読み込み時のファイル更新情報を覚え、保存直前に比べる。"""
from __future__ import annotations

from pathlib import Path

Stamp = tuple[int, int]  # (mtime_ns, size)


class ExternalChangeError(Exception):
    """読み込み後にファイルが外部で変更されている。"""

    def __init__(self, path: Path):
        super().__init__("ファイルが外部で変更されています")
        self.path = path


def stamp(path: Path) -> Stamp | None:
    try:
        st = Path(path).stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def ensure_unchanged(path: Path, expected: Stamp | None) -> None:
    """expected が None（新規など）なら何もしない。違っていれば ExternalChangeError。"""
    if expected is None:
        return
    if stamp(path) != tuple(expected):
        raise ExternalChangeError(Path(path))
