"""エクスポート（zip 書き出し）ブロックの状態遷移と表示（SPEC 10.3章）。Gradio に依存しない純粋関数（テスト可能）。

  ex = {"mode": "scene" | "folder", "cwd": フォルダ一括で表示中のフォルダ（data からの相対パス。scenes/ からはみ出さない）,
        "key": 初期フォルダを作り直す条件（Scene の開き直し）, "selected": None, "pending": None}

フォルダの一覧の動きは保存ブロック（moa.ui.saveblock）と同じ関数を使う（別の状態 ex で動く）。
"""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from moa.core import Repository
from moa.core.export import KIND_LABEL, ExportResult, scenes_in_folder
from moa.core.gen import clean_gen
from moa.ui import saveblock as SB
from moa.ui.logic import build_scene

MODE_LABELS = {"scene": "開いているシーン1つ", "folder": "フォルダ一括"}
CHANGED_NOTE = "保存済みの内容を書き出します（画面の変更は含まれません）"


def new_ex() -> dict:
    return {"mode": "scene", "cwd": "scenes", "key": None, "selected": None, "pending": None}


def set_mode(ex: dict, mode: str) -> dict:
    ex = copy.deepcopy(ex)
    ex["mode"] = "folder" if mode == "folder" else "scene"
    return ex


def sync(repo: Repository, ex: dict, sc: dict) -> dict:
    """Scene を開き直したときだけ、フォルダ一括の初期フォルダ（開いている Scene のフォルダ。なければ scenes/）を作り直す。"""
    ex = copy.deepcopy(ex)
    key = [sc["open_seq"], sc["scene_id"]]
    if ex["key"] != key:
        opened = SB.opened_path(repo, sc)
        ex["cwd"] = repo.rel(opened.parent) if opened is not None else "scenes"
        ex["key"] = key
    return ex


def move(repo: Repository, ex: dict, choice: str | None) -> dict:
    return SB.move(repo, ex, choice)


# ---- シーン1つ ----
def scene_entry(repo: Repository, sc: dict):
    """書き出せる Scene（保存されていて、索引にある）。新規作成中・未保存は None。"""
    return repo.entry("scene", sc["scene_id"]) if sc["scene_id"] else None


def scene_label(repo: Repository, sc: dict) -> str:
    entry = scene_entry(repo, sc)
    if entry is None:
        return "_（保存済みの Scene を開いているときだけ書き出せます。新規作成中は、先に保存してください）_"
    return f"対象：**{entry.obj.name or entry.id}**（`{entry.id}`）  \n`{repo.rel(entry.path)}`"


def screen_changed(repo: Repository, sc: dict, gen_override: dict | None, name: str) -> bool:
    """画面の Scene の定義が、保存されているファイルと違うか（背景・行為・キャスト・服装・ネガ適用・gen・表示名）。

    クイック編集、保存していない生成結果、マスターデータ側の変更は含めない。
    """
    entry = scene_entry(repo, sc)
    if entry is None:
        return False
    saved = entry.obj
    shown = build_scene(sc)
    shown_name = (name or "").strip() or sc["scene_name"] or ""
    shown_cast = {s: (c.character, c.outfit) for s, c in shown.cast.items()}
    saved_cast = {s: (c.character, c.outfit) for s, c in saved.cast.items()}
    return (shown.background != saved.background or shown.action != saved.action or shown_cast != saved_cast
            or shown.apply_negative != saved.apply_negative
            or clean_gen(gen_override) != clean_gen(saved.gen) or shown_name != (saved.name or ""))


# ---- フォルダ一括 ----
def folder_view(repo: Repository, ex: dict) -> tuple[str, list[tuple[str, str]], str]:
    """(パンくず, フォルダの選択肢, 対象の見込み)。"""
    crumbs, folders, _ = SB.listing(repo, ex)
    cwd = SB.cwd_path(repo, ex)
    found, problems = scenes_in_folder(repo, cwd)
    if found:
        names = "、".join(f"`{e.id}`" for e in found)
        preview = f"対象の見込み（_folder.yaml の order の直下の Scene）：{len(found)} 件 — {names}"
        if problems:
            preview += "\n" + "\n".join(f"- ⚠ {p}" for p in problems)
    else:
        preview = "対象の見込み：0 件\n" + "\n".join(f"- {p}" for p in problems)
    return (f"書き出すフォルダ：`{repo.rel(cwd)}/`（{crumbs}）", folders, preview)


# ---- 結果 ----
def result_text(res: ExportResult, changed: bool = False) -> str:
    head = f"⚠ {CHANGED_NOTE}\n\n" if changed else ""
    if not res.ok:
        return (head + "❌ **書き出しを中止しました（zip は作っていません）**\n\n"
                + "\n".join(f"- {p}" for p in res.problems))
    scenes = "\n".join(f"- {name}（`{sid}`）" for name, sid in res.scenes)
    counts = "、".join(f"{KIND_LABEL[k]} {n} 件" for k, n in res.counts.items())
    notes = "".join(f"\n\n⚠ {n}" for n in res.notes)
    return (head + f"✅ **書き出しました**：`{res.filename}`\n\n書き出した Scene：\n{scenes}\n\n"
            f"含めたエンティティ：{counts}{notes}\n\n下のファイル欄からダウンロードできます")


def write_zip(res: ExportResult) -> str | None:
    """ダウンロード用に、一時フォルダへ zip を書く（ファイル名は res.filename のまま）。"""
    if res.zip_bytes is None:
        return None
    path = Path(tempfile.mkdtemp(prefix="moa_export_")) / res.filename
    path.write_bytes(res.zip_bytes)
    return str(path)
