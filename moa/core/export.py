"""エクスポート（SPEC 10.3章）：Scene と、依存する Character / Outfit / Background / Action を zip にまとめる。

Gradio に依存しない。読み込み・参照解決は既存の Repository（id→パス索引）を使う。
エンティティのファイルは加工せずにコピーする。例外は、`_folder.yaml` の order の取り除きと、Scene の生成の記録の除去だけ。
依存切れ・id の重複があれば中止し、zip は作らない。
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .constants import NONE, ROOTS
from .repository import Entry, Repository
from .yaml_io import load_roundtrip, make_yaml

GENERATION_KEYS = ("seed", "images", "gen", "partial")   # 例外2：compiled から取り除く4項目
KIND_ORDER = ("scene", "action", "background", "character", "outfit")
KIND_LABEL = {"scene": "Scene", "action": "Action", "background": "Background", "character": "Character", "outfit": "Outfit"}
ROOT_OF = {kind: root for root, kind in ROOTS.items()}
LOCAL_PATH_NOTE = "画像のパスは、このPCの中の場所です"


@dataclass
class ExportResult:
    ok: bool = False
    zip_bytes: bytes | None = None
    filename: str = ""
    scenes: list[tuple[str, str]] = field(default_factory=list)   # (表示名, id)
    counts: dict[str, int] = field(default_factory=dict)          # 種類 → 件数（エンティティ。_folder.yaml は数えない）
    files: list[str] = field(default_factory=list)                # zip に入れたパス（並び順どおり）
    problems: list[str] = field(default_factory=list)             # 中止の理由
    notes: list[str] = field(default_factory=list)


def _rel(repo: Repository, path: Path) -> str:
    return repo.rel(path)


# ---- 対象 ----
def scenes_in_folder(repo: Repository, folder: Path) -> tuple[list[Entry], list[str]]:
    """folder の `_folder.yaml` の order に書かれた、直下の Scene。(対象, 問題・理由)。

    order に無い Scene、サブフォルダの中は対象外（警告も出さない）。対象が0件なら、理由を問題として返す。
    order にあるのに読み込めない Scene（スキーマ違反・id の重複など）は、問題として返す［暫定：中止にする］。
    """
    folder = folder.resolve()
    meta_path = folder / "_folder.yaml"
    meta = repo.folder_meta.get(folder)
    if not meta_path.is_file():
        return [], [f"`{_rel(repo, folder)}` に _folder.yaml がありません"]
    if meta is None:
        return [], [f"`{_rel(repo, meta_path)}` を読み込めません"]
    if not meta.order:
        return [], [f"`{_rel(repo, meta_path)}` に order がありません（または空です）"]
    found: list[Entry] = []
    problems: list[str] = []
    for name in meta.order:
        if not name.endswith(".yaml"):
            continue
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            continue
        entry = repo.by_path.get(path)
        if entry is not None and entry.kind == "scene":
            if entry not in found:
                found.append(entry)
        elif entry is None and path.name != "_folder.yaml":
            problems.append(f"order の `{name}` は読み込めていません（スキーマ違反・id の重複など）。"
                            "先にデータの検証結果を確認してください")
    if not found and not problems:
        problems.append(f"`{_rel(repo, meta_path)}` の order に、直下の Scene がありません")
    return found, problems


# ---- 依存の閉包 ----
@dataclass
class Closure:
    entries: dict[tuple[str, str], Entry] = field(default_factory=dict)
    missing: list[tuple[str, str]] = field(default_factory=list)      # 見つからない参照の説明（重複を除く）
    seen_missing: set = field(default_factory=set)


def collect(repo: Repository, scenes: list[Entry]) -> Closure:
    """Scene から依存をすべて辿る（none を除く。同じエンティティは1回）。"""
    cl = Closure()
    work: list[Entry] = []

    def add(entry: Entry) -> None:
        key = (entry.kind, entry.id)
        if key not in cl.entries:
            cl.entries[key] = entry
            work.append(entry)

    def ref(src: Entry, where: str, kind: str, eid: str | None) -> None:
        if not eid or eid == NONE:
            return
        target = repo.entry(kind, eid)
        if target is not None:
            add(target)
            return
        key = (src.kind, src.id, where, kind, eid)
        if key not in cl.seen_missing:
            cl.seen_missing.add(key)
            cl.missing.append((f"{KIND_LABEL[src.kind]} `{src.id}` の {where}", f"{KIND_LABEL[kind]} `{eid}`"))

    for s in scenes:
        add(s)
    while work:
        e = work.pop(0)
        o = e.obj
        if e.kind == "scene":
            ref(e, "background", "background", o.background)
            ref(e, "action", "action", o.action)
            for slot, c in o.cast.items():
                ref(e, f"cast.{slot}.character", "character", c.character)
                ref(e, f"cast.{slot}.outfit", "outfit", c.outfit)
        elif e.kind == "action":
            for slot, oid in o.outfits.items():
                ref(e, f"outfits.{slot}", "outfit", oid)
            ref(e, "background（推奨背景）", "background", o.background)
        elif e.kind == "character":
            ref(e, "defaultOutfit", "outfit", o.default_outfit)
    return cl


def duplicate_problems(repo: Repository, cl: Closure) -> list[str]:
    out = []
    for (kind, eid), paths in sorted(repo.duplicates.items()):
        if (kind, eid) in cl.entries:
            where = "、".join(f"`{_rel(repo, p)}`" for p in paths)
            out.append(f"{KIND_LABEL[kind]} `{eid}` が重複しています（{where}）")
    return out


# ---- zip の組み立て ----
def _dump(doc) -> bytes:
    buf = io.StringIO()
    make_yaml().dump(doc, buf)
    return buf.getvalue().encode("utf-8")


def strip_generation_record(raw: bytes, path: Path) -> bytes:
    """Scene の compiled から seed / images / gen / partial だけを取り除く。該当が無ければ元のバイト列のまま。"""
    doc = load_roundtrip(path)
    compiled = doc.get("compiled") if hasattr(doc, "get") else None
    if not hasattr(compiled, "keys") or not any(k in compiled for k in GENERATION_KEYS):
        return raw
    for k in GENERATION_KEYS:
        if k in compiled:
            del compiled[k]
    return _dump(doc)


def trim_folder_order(raw: bytes, path: Path, keep: set[str]) -> bytes:
    """`_folder.yaml` の order から、keep に無い名前を取り除く（order 以外・コメント・書式はそのまま）。取り除くものが無ければ元のまま。"""
    doc = load_roundtrip(path)
    order = doc.get("order") if hasattr(doc, "get") else None
    if order is None or not any(name not in keep for name in order):
        return raw
    for i in reversed(range(len(order))):
        if order[i] not in keep:
            del order[i]
    return _dump(doc)


def build_zip(repo: Repository, cl: Closure, strip_generation: bool) -> tuple[bytes, list[str]]:
    """(zip のバイト列, zip に入れたパス)。パスは <ルート名>/<元の相対パス>（ラッパの階層なし）。"""
    files: dict[str, bytes] = {}
    for entry in cl.entries.values():
        raw = entry.path.read_bytes()
        if entry.kind == "scene" and strip_generation:
            raw = strip_generation_record(raw, entry.path)
        files[_rel(repo, entry.path)] = raw

    # 例外1：書き出すファイルが入っているフォルダ（途中の階層・種類のルートまで）の _folder.yaml
    names_in: dict[Path, set[str]] = {}   # フォルダ → そのフォルダの中で zip に入るファイル・フォルダの名前
    for entry in cl.entries.values():
        root = repo.root_dir(entry.kind)
        child, parent = entry.path, entry.path.parent
        while True:
            names_in.setdefault(parent, set()).add(child.name)
            if parent == root:
                break
            child, parent = parent, parent.parent
    for folder, keep in names_in.items():
        meta = folder / "_folder.yaml"
        if meta.is_file():
            files[_rel(repo, meta)] = trim_folder_order(meta.read_bytes(), meta, keep)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    return buf.getvalue(), sorted(files)


# ---- 入口 ----
def _finish(repo: Repository, scenes: list[Entry], strip_generation: bool, filename: str,
            problems: list[str]) -> ExportResult:
    res = ExportResult(filename=filename, problems=list(problems))
    if res.problems or not scenes:
        return res
    cl = collect(repo, scenes)
    res.problems += [f"{src} が参照する {dst} が見つかりません" for src, dst in cl.missing]
    res.problems += duplicate_problems(repo, cl)
    if res.problems:
        return res
    res.zip_bytes, res.files = build_zip(repo, cl, strip_generation)
    res.scenes = [(getattr(s.obj, "name", None) or s.id, s.id) for s in scenes]
    res.counts = {k: n for k in KIND_ORDER if (n := sum(1 for kind, _ in cl.entries if kind == k))}
    if not strip_generation:
        res.notes.append(LOCAL_PATH_NOTE)
    res.ok = True
    return res


def export_scene(root: Path | str, scene_id: str, strip_generation: bool = True) -> ExportResult:
    """保存されている Scene 1つを書き出す。ディスクの今の状態を読み直して判定する。"""
    repo = Repository(root)
    entry = repo.entry("scene", scene_id)
    if entry is None:
        problems = [f"Scene `{scene_id}` が見つかりません（保存されていない、または読み込めません）"]
        if (kind := ("scene", scene_id)) in repo.duplicates:
            problems = [f"Scene `{scene_id}` が重複しています（"
                        + "、".join(f"`{_rel(repo, p)}`" for p in repo.duplicates[kind]) + "）"]
        return ExportResult(filename=f"{scene_id}_export.zip", problems=problems)
    return _finish(repo, [entry], strip_generation, f"{scene_id}_export.zip", [])


def export_folder(root: Path | str, folder: Path, strip_generation: bool = True) -> ExportResult:
    """フォルダ直下の、`_folder.yaml` の order に書かれた Scene を書き出す。"""
    repo = Repository(root)
    folder = Path(folder).resolve()
    scenes, problems = scenes_in_folder(repo, folder)
    return _finish(repo, scenes, strip_generation, f"{folder.name}_export.zip", problems)
