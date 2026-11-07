"""scenes/ のフォルダ情報（`_folder.yaml`）の編集の状態遷移（SPEC 10.4章）。Gradio に依存しない純粋関数（テスト可能）。

order は「並び順」であり、フォルダ一括エクスポートなどの「対象の一覧」でもある。そのため、実在する項目を自動では order に入れない。

  fe = {"folder": 編集中のフォルダ（data からの相対パス。未読み込みは None）,
        "rows": [{"name": 名前, "kind": "dir" | "file", "included": order に含める, "missing": ディスクに無い}],
        "selected": 一覧で選んでいる名前,
        "stamp": 読み込み時の _folder.yaml の更新情報（外部変更の検知用。ファイルが無ければ None）,
        "name" / "description": 読み込み時の値, "loaded_rows": 読み込み時の [(名前, included)]（未保存の判定用）}

保存・検証・書き戻しは moa.core.foldermeta の共通処理（エディタと同じ）を使う。
"""
from __future__ import annotations

import copy
from pathlib import Path

from moa.core import Repository
from moa.core.filestamp import stamp
from moa.core.foldermeta import ERRORS, EXTERNAL, write_folder_meta
from moa.core.textutil import natural_key

FOLDER_FILE = "_folder.yaml"
EXTERNAL_MSG = "⚠ _folder.yaml が外部で変更されています（VS Code・エクスプローラーなど）。読み直すか、上書きするか選んでください"


def new_fe() -> dict:
    return {"folder": None, "rows": [], "selected": None, "stamp": None, "name": "", "description": "",
            "loaded_rows": []}


# ---- 読み込み ----
def existing_items(folder: Path) -> dict[str, str]:
    """そのフォルダ直下に実在する項目：{名前: "dir" | "file"}。

    Scene ファイル（`.yaml`。`_folder.yaml` を除く）とサブフォルダ（名前が `_` `.` で始まるものを除く）。
    読み込めないファイル（スキーマ違反など）も実在として数える。
    """
    items: dict[str, str] = {}
    if not folder.is_dir():
        return items
    for child in folder.iterdir():
        if child.is_dir():
            if not child.name.startswith(("_", ".")):
                items[child.name] = "dir"
        elif child.suffix == ".yaml" and child.name != FOLDER_FILE and not child.name.startswith("."):
            items[child.name] = "file"
    return items


def open_folder(repo: Repository, folder: Path, selected: str | None = None) -> dict:
    """フォルダの `_folder.yaml` を一覧にする。order に書かれた項目が ☑ で先頭、書かれていない実在の項目は ☐ で後ろ。"""
    folder = Path(folder).resolve()
    meta = repo.folder_meta.get(folder)
    existing = existing_items(folder)
    rows: list[dict] = []
    seen: set[str] = set()
    for name in (meta.order if meta else []):
        if name in seen:   # 重複は最初の1つにまとめる
            continue
        seen.add(name)
        if name in existing:
            rows.append({"name": name, "kind": existing[name], "included": True, "missing": False})
        else:              # ディスクに無い名前（保存時に取り除く）
            rows.append({"name": name, "kind": "file", "included": True, "missing": True})
    rest = sorted((n for n in existing if n not in seen), key=lambda n: (existing[n] != "dir", natural_key(n)))
    rows += [{"name": n, "kind": existing[n], "included": False, "missing": False} for n in rest]
    fe = new_fe()
    fe.update(folder=repo.rel(folder), rows=rows, stamp=list(stamp(folder / FOLDER_FILE) or ()) or None,
              name=(meta.name if meta else None) or "", description=(meta.description if meta else None) or "",
              loaded_rows=[(r["name"], r["included"]) for r in rows])
    fe["selected"] = selected if any(r["name"] == selected for r in rows) else None
    return fe


# ---- 一覧の操作 ----
def select(fe: dict, name: str | None) -> dict:
    fe = copy.deepcopy(fe)
    fe["selected"] = name if any(r["name"] == name for r in fe["rows"]) else None
    return fe


def _index(fe: dict) -> int | None:
    return next((i for i, r in enumerate(fe["rows"]) if r["name"] == fe["selected"]), None)


def toggle(fe: dict) -> tuple[dict, str]:
    """選んだ項目の「order に含める」を切り替える（並びは変えない）。見つからない項目は切り替えられない（保存時に取り除かれる）。"""
    i = _index(fe)
    if i is None:
        return fe, "⚠ 先に一覧から項目を選んでください"
    if fe["rows"][i]["missing"]:
        return fe, "⚠ 見つからない項目は切り替えられません（保存すると order から取り除かれます）"
    fe = copy.deepcopy(fe)
    fe["rows"][i]["included"] = not fe["rows"][i]["included"]
    return fe, ""


def move(fe: dict, direction: str) -> tuple[dict, str]:
    """選んだ項目を1つ上（"up"）／下（"down"）へ動かす。端では何もしない。"""
    i = _index(fe)
    if i is None:
        return fe, "⚠ 先に一覧から項目を選んでください"
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(fe["rows"]):
        return fe, ""
    fe = copy.deepcopy(fe)
    fe["rows"][i], fe["rows"][j] = fe["rows"][j], fe["rows"][i]
    return fe, ""


# ---- 表示 ----
def labels(fe: dict) -> list[tuple[str, str]]:
    """一覧（Radio）の選択肢 [(表示, 名前)]。☑ には、order に書かれる順の番号を付ける。"""
    out, n = [], 0
    for r in fe["rows"]:
        icon = "📁" if r["kind"] == "dir" else "🎬"
        if r["missing"]:
            out.append((f"⚠ {r['name']}（見つからない）", r["name"]))
        elif r["included"]:
            n += 1
            out.append((f"☑ {n}. {icon} {r['name']}", r["name"]))
        else:
            out.append((f"☐ {icon} {r['name']}", r["name"]))
    return out


def removed_names(fe: dict) -> list[str]:
    """保存で order から取り除かれる名前（ディスクに無い項目）。"""
    return [r["name"] for r in fe["rows"] if r["missing"]]


def removed_text(fe: dict) -> str:
    names = removed_names(fe)
    if not names:
        return ""
    return "⚠ 保存すると、order から次の名前を取り除きます（ディスク上に無い項目）：" + "、".join(f"`{n}`" for n in names)


def order_to_write(fe: dict) -> list[str]:
    """保存で order に書く名前：☑ の項目だけを、表示している順で。"""
    return [r["name"] for r in fe["rows"] if r["included"] and not r["missing"]]


def dirty(fe: dict, name: str, description: str) -> bool:
    """読み込み時から変わっているか（キー入力ごとには呼ばない）。"""
    if fe["folder"] is None:
        return False
    return ((name or "").strip() != fe["name"].strip() or (description or "").strip() != fe["description"].strip()
            or [(r["name"], r["included"]) for r in fe["rows"]] != fe["loaded_rows"])


# ---- 保存 ----
def save(repo: Repository, fe: dict, name: str, description: str, *, force: bool = False) -> tuple[dict, str, bool]:
    """(新しい fe, メッセージ, 外部変更で止まったか)。エラー・外部変更のときは fe を変えず、書かない。"""
    if fe["folder"] is None:
        return fe, "⚠ フォルダが選ばれていません", False
    folder = (repo.root / fe["folder"]).resolve()
    removed = removed_names(fe)
    res = write_folder_meta(repo, folder, name or "", description or "", order_to_write(fe),
                            expected_stamp=fe["stamp"], force=force)
    if res.status == EXTERNAL:
        return fe, EXTERNAL_MSG, True
    if res.status == ERRORS:
        return fe, "❌ " + res.message + "\n\n" + "\n".join(f"- {i.message}" for i in res.issues), False
    msg = f"💾 保存しました：`{res.path}`（order {len(order_to_write(fe))} 件）"
    if removed:
        msg += "\n\n取り除いた見つからない項目：" + "、".join(f"`{n}`" for n in removed)
    return open_folder(repo, folder, fe["selected"]), msg, False
