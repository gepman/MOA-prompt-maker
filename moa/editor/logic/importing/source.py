"""インポートの入力（EDITOR_SPEC 15.1・15.5・15.9章）。

単一の .yaml・zip・フォルダを、取り込み候補のファイル一覧（`SourceFile`）と、取り込まないもの（`Ignored`）にする。
zip は展開せず、メモリで読む（配置先の外に書き出す余地を作らない）。ここでは何も書き込まない。
"""
from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from moa.core.constants import ROOTS

ROOT_NAMES = tuple(ROOTS)            # characters / outfits / backgrounds / actions / scenes
FOLDER_FILE = "_folder.yaml"

# zip 爆弾などへの備え。超えたら入力ごと却下する
MAX_FILES = 3000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class SourceFile:
    path: str      # 入力の中の相対パス（/ 区切り）
    data: bytes


@dataclass(frozen=True)
class Ignored:
    path: str
    reason: str


@dataclass
class Batch:
    files: list[SourceFile] = field(default_factory=list)
    ignored: list[Ignored] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None   # 入力ごと却下する理由


def classify_path(raw: str) -> tuple[str | None, str | None]:
    """(正規化した相対パス, 取り込まない理由)。どちらか一方だけが入る。"""
    p = raw.replace("\\", "/")
    if p.startswith("/") or (len(p) >= 2 and p[1] == ":"):
        return None, "配置先の外に出るパス（絶対パス）"
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts:
        return None, "パスが空です"
    if ".." in parts:
        return None, "配置先の外に出るパス（..）"
    for part in parts[:-1]:
        if part == "__MACOSX" or part.startswith("."):
            return None, f"システム用のフォルダ（{part}）"
    name = parts[-1]
    if name.startswith("."):
        return None, "システム用のファイル"
    if not name.lower().endswith(".yaml"):
        return None, "拡張子が .yaml ではありません"
    if name.lower() == FOLDER_FILE:
        parts[-1] = FOLDER_FILE
    return "/".join(parts), None


def _zip_name(info: zipfile.ZipInfo) -> str:
    """zip 内の名前。UTF-8 のフラグが無い名前は、UTF-8 として読めればそれ、だめなら cp932 とみなす。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _add(batch: Batch, seen: set[str], shown: str, rel: str | None, why: str | None, data: bytes | None) -> None:
    if why is not None or rel is None:
        batch.ignored.append(Ignored(shown, why or "取り込めません"))
    elif rel in seen:
        batch.ignored.append(Ignored(shown, "同じパスが重複しています（後のものを無視）"))
    elif data is None:
        batch.ignored.append(Ignored(shown, "読み込めません"))
    else:
        seen.add(rel)
        batch.files.append(SourceFile(rel, data))


def read_zip(path: Path) -> Batch:
    batch = Batch()
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        batch.error = f"zip として読めません：{e}"
        return batch
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            batch.error = f"zip のファイル数が多すぎます（{len(infos)} 件。上限 {MAX_FILES} 件）"
            return batch
        seen: set[str] = set()
        total = 0
        for info in infos:
            shown = _zip_name(info)
            rel, why = classify_path(shown)
            if why is None and info.file_size > MAX_FILE_BYTES:
                why = f"大きすぎます（{info.file_size} バイト。上限 {MAX_FILE_BYTES}）"
            data = None
            if why is None:
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    batch.error = f"zip の合計の大きさが上限（{MAX_TOTAL_BYTES} バイト）を超えています"
                    return batch
                try:
                    with zf.open(info) as f:
                        data = f.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        data, why = None, "大きすぎます"
                except (RuntimeError, zipfile.BadZipFile, OSError) as e:
                    why = f"読み込めません（{e}）"
            _add(batch, seen, shown, rel, why, data)
    return batch


def read_folder(top: Path) -> Batch:
    """フォルダ配下の .yaml を再帰的に読む。選んだフォルダ自体の名前がルート名なら、その名前も相対パスの先頭に含める。"""
    batch = Batch()
    base = top.parent if top.name in ROOT_NAMES else top
    seen: set[str] = set()
    count = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        keep = []
        for d in sorted(dirnames):
            if d == "__MACOSX" or d.startswith(".") or (Path(dirpath) / d).is_symlink():
                shown = (Path(dirpath) / d).relative_to(base).as_posix()
                batch.ignored.append(Ignored(shown, "システム用のフォルダ（またはシンボリックリンク）"))
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            full = Path(dirpath) / name
            shown = full.relative_to(base).as_posix()
            rel, why = classify_path(shown)
            data = None
            if why is None and full.is_symlink():
                why = "シンボリックリンクは取り込めません"
            if why is None:
                count += 1
                size = full.stat().st_size
                if count > MAX_FILES:
                    batch.error = f"ファイル数が多すぎます（上限 {MAX_FILES} 件）"
                    return batch
                if size > MAX_FILE_BYTES:
                    why = f"大きすぎます（{size} バイト。上限 {MAX_FILE_BYTES}）"
                else:
                    total += size
                    if total > MAX_TOTAL_BYTES:
                        batch.error = f"合計の大きさが上限（{MAX_TOTAL_BYTES} バイト）を超えています"
                        return batch
                    try:
                        data = full.read_bytes()
                    except OSError as e:
                        why = f"読み込めません（{e}）"
            _add(batch, seen, shown, rel, why, data)
    return batch


def read_single(path: Path) -> Batch:
    batch = Batch()
    rel, why = classify_path(path.name)
    try:
        data = path.read_bytes() if why is None else None
    except OSError as e:
        batch.error = f"読み込めません：{e}"
        return batch
    _add(batch, set(), path.name, rel, why, data)
    return batch


def strip_wrapper(batch: Batch) -> None:
    """全ファイルの一番上が同じ1つのフォルダ（ルート名でない）で、その直下がルート名なら、そのフォルダを外す（15.5章）。"""
    files = batch.files
    if not files:
        return
    split = [f.path.split("/") for f in files]
    first = {p[0] for p in split}
    if len(first) != 1 or any(len(p) < 2 for p in split):
        return
    wrapper = next(iter(first))
    if wrapper in ROOT_NAMES or not any(len(p) >= 3 and p[1] in ROOT_NAMES for p in split):
        return
    batch.files = [SourceFile("/".join(p[1:]), f.data) for p, f in zip(split, files)]
    batch.notes.append(f"ラッパー {wrapper} を外しました（{wrapper}/ の直下をルートとして扱います）")


def read_batch(path: str | Path) -> Batch:
    """zip・フォルダ・単一の .yaml のどれかを読む。ラッパーは外す。何も書き込まない。"""
    p = Path(path)
    if p.is_dir():
        batch = read_folder(p)
    elif p.suffix.lower() == ".zip":
        batch = read_zip(p)
    elif p.suffix.lower() == ".yaml":
        batch = read_single(p)
    else:
        return Batch(error="対応していない入力です（.yaml、.zip、またはフォルダを指定してください）")
    if batch.error is None:
        strip_wrapper(batch)
    return batch
