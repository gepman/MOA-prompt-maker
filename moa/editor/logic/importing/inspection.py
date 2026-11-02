"""検査（EDITOR_SPEC 15.3・15.4章）：ファイルごとに読み、id と種類を判定する。**書き込まない**。

ここでは「そのファイルだけで決まること」（読めるか・id・種類・フォルダ名）を見る。バッチ全体や既存との関係
（重複・競合・参照・配置先・検証）は `plan.resolve` が、選択（種類・別 ID など）を踏まえて見る。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ruamel.yaml import YAML

from moa.core.constants import ID_RE, NONE, ROOTS
from moa.core.folders import check_folder_name
from moa.core.repository import KINDS, Repository

from .source import FOLDER_FILE, ROOT_NAMES, Batch, Ignored, SourceFile

KIND_LABEL = {"character": "Character", "outfit": "Outfit", "background": "Background", "action": "Action",
              "scene": "Scene", "folder": "フォルダ設定（_folder.yaml）"}
MAX_MESSAGES = 5


@dataclass
class Candidate:
    path: str                                   # 入力の中の相対パス
    parts: tuple[str, ...]                      # フォルダの部分（ファイル名を除く）
    text: str = ""                              # 元のテキスト（BOM なし）
    data: dict | None = None                    # YAML として読んだ値
    kind: str | None = None                     # 5種のどれか、または "folder"
    kinds: list[str] = field(default_factory=list)   # 複数の種類に適合するとき、その候補
    id: str | None = None
    hint: str | None = None                     # パスの手がかりから決まる種類
    rejected: str | None = None                 # 却下の理由

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"

    @property
    def ambiguous(self) -> bool:
        return self.rejected is None and self.kind is None and len(self.kinds) > 1


@dataclass
class Inspection:
    candidates: list[Candidate] = field(default_factory=list)
    ignored: list[Ignored] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def hint_kind(parts: tuple[str, ...]) -> str | None:
    """パスにルート名が含まれていれば、その種類（複数あれば、いちばん外側）。"""
    for part in parts:
        if part in ROOT_NAMES:
            return ROOTS[part]
    return None


def _short(messages: list[str]) -> str:
    shown = messages[:MAX_MESSAGES]
    rest = len(messages) - len(shown)
    return "；".join(shown) + (f"（ほか {rest} 件）" if rest > 0 else "")


def _schema_messages(repo: Repository, kind: str, data: dict) -> list[str]:
    return [f"スキーマ違反 {m}" for m in repo.schema_errors(kind, data)]


def inspect_file(repo: Repository, sf: SourceFile) -> Candidate:
    parts = tuple(sf.path.split("/")[:-1])
    name = sf.path.split("/")[-1]
    cand = Candidate(sf.path, parts, hint=hint_kind(parts))

    try:
        cand.text = sf.data.decode("utf-8-sig")
    except UnicodeDecodeError:
        cand.rejected = "UTF-8 として読めません"
        return cand
    try:
        data = YAML(typ="safe").load(cand.text)
    except Exception as e:  # noqa: BLE001（YAML の構文エラーなど。理由を出して却下する）
        cand.rejected = f"YAML として読めません：{str(e).splitlines()[0] if str(e) else type(e).__name__}"
        return cand
    if not isinstance(data, dict):
        cand.rejected = "トップレベルがマッピングではありません"
        return cand
    cand.data = data

    for part in parts:   # 15.5章：フォルダ名の検査（使えない名前のフォルダにあるファイルは却下）
        try:
            check_folder_name(part)
        except ValueError as e:
            cand.rejected = f"フォルダ名 '{part}' が使えません：{e}"
            return cand

    if name == FOLDER_FILE:
        cand.kind = "folder"
        errs = _schema_messages(repo, "folder", data)
        if errs:
            cand.rejected = _short(errs)
        return cand

    eid = data.get("id")
    if "id" in data:
        if not isinstance(eid, str):
            cand.rejected = f"IDが文字列でない（クォート漏れの疑い）：id の値が {type(eid).__name__}（{eid!r}）として読まれました"
            return cand
        if not ID_RE.match(eid):
            cand.rejected = f"id '{eid}' は ^[a-z0-9_]+$ に合いません"
            return cand
        cand.id = eid
    if not str(data.get("name") or "").strip():
        cand.rejected = "name がありません（空は不可）"
        return cand

    errors = {k: _schema_messages(repo, k, data) for k in KINDS}
    fitting = [k for k in KINDS if not errors[k]]
    if cand.id == NONE:   # Outfit の id に none は使えない（Outfit のスキーマも弾く）
        only_id = bool(errors["outfit"]) and all(m.startswith("スキーマ違反 id:") for m in errors["outfit"])
        fitting = [k for k in fitting if k != "outfit"]
        if cand.hint == "outfit" or (not fitting and (not errors["outfit"] or only_id)):
            cand.rejected = "Outfit の id に `none` は使えません"
            return cand

    if cand.hint is not None:
        if cand.hint in fitting:
            cand.kind = cand.hint
        elif fitting:
            cand.rejected = (f"パスは {cand.hint} のものですが、中身は {', '.join(fitting)} のスキーマに適合します"
                             "（パスと中身が合いません）")
        else:
            cand.rejected = f"{KIND_LABEL[cand.hint]} として不適合：" + _short(errors[cand.hint])
        return cand

    if len(fitting) == 1:
        cand.kind = fitting[0]
    elif len(fitting) > 1:
        cand.kinds = fitting   # 確認画面で種類を選ばせる
    else:
        closest = min(KINDS, key=lambda k: len(errors[k]))
        cand.rejected = f"どの種類にも適合しません（最も近い {closest} との差：{_short(errors[closest])}）"
    return cand


def inspect_batch(repo: Repository, batch: Batch) -> Inspection:
    """バッチの全ファイルを検査する。書き込まない。"""
    insp = Inspection(ignored=list(batch.ignored), notes=list(batch.notes), error=batch.error)
    if batch.error is None:
        insp.candidates = [inspect_file(repo, sf) for sf in batch.files]
    return insp
