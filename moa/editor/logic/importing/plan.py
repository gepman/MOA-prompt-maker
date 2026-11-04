"""取り込みの計画（EDITOR_SPEC 15.2・15.5〜15.8・15.10章）。`resolve` は**書き込まない**。

検査（`inspection`）の結果と、確認画面での選択（`Choices`）から、ファイルごと・フォルダごとに「何をするか」を決める。
確認画面の表示にも、書き込み直前の再確認（`apply.apply_plan`）にも同じ関数を使う。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from moa.core.constants import ID_RE, NONE
from moa.core.models import MISSING_REF, Action, Background, Character, Issue, Outfit, Scene
from moa.core.overlay import OverlayRepository
from moa.core.repository import KINDS, Repository
from moa.core.usage import scenes_using_action, scenes_using_background
from moa.core.validate import scene_overflow, validate_scene
from moa.editor.logic.validation import id_taken, object_issues, slot_issues

from .inspection import KIND_LABEL, Candidate, Inspection, _short
from .source import FOLDER_FILE, ROOT_NAMES, Ignored

# ファイルごとの結果
CREATE = "create"                    # 新規に取り込む
RENAMED = "create_renamed"           # 別 ID で取り込む
OVERWRITE = "overwrite"              # 同じ id の既存ファイルを、取り込み側の内容で置き換える（確認画面で選んだものだけ）
SKIP_SAME = "skip_same"              # すでに同じ内容がある
SKIP_CHOICE = "skip_choice"          # 同じ id があり、スキップを選んだ（初期値）
SKIP_UNSELECTED = "skip_unselected"  # 種類が複数あり、選ばれていない
REJECT = "reject"
WRITES = (CREATE, RENAMED, OVERWRITE)

# フォルダ設定（_folder.yaml）の結果
F_CREATE = "create"        # バッチのものをそのまま書く
F_MERGE = "merge"          # 既存と合成して書く
F_SKIP_SAME = "skip_same"  # 合成結果が既存と同じなので書かない
F_SKIP_LABEL = "skip_label"  # 既存のフォルダに _folder.yaml が無く、表示名を付けない（初期値）
F_REJECT = "reject"

_BUILDERS = {"character": Character.from_dict, "outfit": Outfit.from_dict, "background": Background.from_dict,
             "action": Action.from_dict, "scene": Scene.from_dict}


@dataclass
class Choices:
    """確認画面での選択。"""
    dest: dict[str, str] = field(default_factory=dict)    # 種類 → 配置先フォルダ（data からの相対パス）。無ければそのルート直下
    kinds: dict[str, str] = field(default_factory=dict)   # ファイルのパス → 選んだ種類（複数に適合するとき）
    rename: dict[str, str] = field(default_factory=dict)  # ファイルのパス → 別 ID（空・無しならスキップ）
    overwrite: set[str] = field(default_factory=set)      # 既存を上書きするファイルのパス（無ければスキップ）
    desc: dict[str, str] = field(default_factory=dict)    # フォルダ（相対パス）→ "existing" | "import"
    label: dict[str, bool] = field(default_factory=dict)  # フォルダ（相対パス）→ 表示名を付ける


@dataclass
class Item:
    path: str                       # 入力の中のパス
    kind: str | None = None
    id: str | None = None           # 元の id
    final_id: str | None = None     # 書き込む id（別 ID のとき新しい id）
    action: str = ""
    dest: Path | None = None        # 書き込み先（取り込むもの）
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    conflict: bool = False          # 同じ id が既にあり、中身が違う
    existing: str = ""              # その既存ファイル（data からの相対パス）
    suggested: str = ""             # 別 ID の初期値
    existing_path: Path | None = None   # 既存のファイル（上書きの書き込み先）
    existing_sig: str = ""          # 既存ファイルの中身の指紋（確認画面のあとに変わっていないかの確認用）
    candidates: list[str] = field(default_factory=list)   # 種類の候補（複数に適合するとき）
    by_choice: bool = False         # 配置先を選ぶ必要がある（一番上がルート名でない）

    @property
    def writes(self) -> bool:
        return self.action in WRITES


@dataclass
class FolderItem:
    path: str
    dest_dir: Path | None = None
    key: str = ""                   # 配置先フォルダ（data からの相対パス）。確認画面の選択のキー
    action: str = ""
    reason: str = ""
    text: str = ""                  # F_CREATE：そのまま書くテキスト
    merged: dict | None = None      # F_MERGE：書く name / description / order
    desc_conflict: tuple[str, str] | None = None    # 違う description（既存, 取り込み側）
    label_choice: bool = False      # 「表示名を付ける／付けない」を選ぶ対象
    import_name: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def writes(self) -> bool:
        return self.action in (F_CREATE, F_MERGE)


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    folders: list[FolderItem] = field(default_factory=list)
    ignored: list[Ignored] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    needs_dest: list[str] = field(default_factory=list)   # 配置先を選ぶ必要がある種類
    choices: "Choices" = field(default_factory=lambda: Choices())   # この計画を作ったときの選択

    def item(self, path: str) -> Item | None:
        return next((i for i in self.items if i.path == path), None)


def _sig(path: Path | None) -> str:
    """ファイルの中身の指紋。読めなければ空文字。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else ""
    except OSError:
        return ""


def _suggest(repo: Repository, kind: str, eid: str, used: dict[str, set[str]]) -> str:
    """別 ID の初期値：`<元のid>_copy`、使われていれば `_copy2`、`_copy3` …"""
    base = f"{eid}_copy"
    cand, n = base, 1
    while cand in used[kind] or id_taken(repo, kind, cand):
        n += 1
        cand = f"{base}{n}"
    return cand


def _check_new_id(repo: Repository, kind: str, new: str, used: dict[str, set[str]]) -> str:
    """新しい ID の検査（新規 ID と同じ規則）。問題があれば理由、なければ空文字。"""
    if not ID_RE.match(new):
        return "半角英小文字・数字・アンダースコアだけで入力してください"
    if kind == "outfit" and new == NONE:
        return "Outfit の id に `none` は使えません"
    if new in used[kind]:
        return "バッチ内の別のファイルが同じ id で取り込まれます"
    if id_taken(repo, kind, new):
        return f"id '{new}' は既に使われています（{kind}）"
    return ""


def _validate(ov: OverlayRepository, kind: str, obj) -> list[Issue]:
    if kind == "scene":
        return validate_scene(ov, obj) + scene_overflow(ov, obj)
    return object_issues(ov, kind, obj)


def _refs(kind: str, obj) -> list[tuple[str, str]]:
    """そのオブジェクトが参照している (種類, id)（15.10章）。"""
    out: list[tuple[str, str]] = []
    if kind == "character":
        if obj.default_outfit and obj.default_outfit != NONE:
            out.append(("outfit", obj.default_outfit))
    elif kind == "action":
        out += [("outfit", v) for v in obj.outfits.values() if v != NONE]
        if obj.background:
            out.append(("background", obj.background))
    elif kind == "scene":
        out += [("background", obj.background), ("action", obj.action)]
        for e in obj.cast.values():
            out.append(("character", e.character))
            if e.outfit not in (None, NONE):
                out.append(("outfit", e.outfit))
    return out


def resolve(repo: Repository, insp: Inspection, choices: Choices | None = None) -> Plan:
    """検査の結果と選択から、計画を作る。何も書き込まない。"""
    choices = choices or Choices()
    plan = Plan(ignored=list(insp.ignored), notes=list(insp.notes), error=insp.error, choices=choices)
    if insp.error:
        return plan
    cands: dict[str, Candidate] = {c.path: c for c in insp.candidates}
    items: list[Item] = []

    # 1. 種類の確定
    for c in insp.candidates:
        if c.is_folder:
            continue
        it = Item(c.path, id=c.id, candidates=list(c.kinds))
        if c.rejected:
            it.action, it.reason = REJECT, c.rejected
        elif c.kind:
            it.kind = c.kind
        elif choices.kinds.get(c.path) in c.kinds:
            it.kind = choices.kinds[c.path]
        else:
            it.action = SKIP_UNSELECTED
            it.reason = f"複数の種類（{', '.join(c.kinds)}）に適合します。取り込む種類を選んでください（選ばないと取り込みません）"
        items.append(it)
    plan.items = items

    # 2. バッチ内の重複（同じ種類・同じ id）は、すべて却下
    groups: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in items:
        if not it.action:
            groups[(it.kind, it.id)].append(it)
    for (kind, eid), group in groups.items():
        if len(group) > 1:
            paths = "、".join(g.path for g in group)
            for g in group:
                g.action = REJECT
                g.reason = f"同じ種類（{kind}）・同じ id '{eid}' のファイルがバッチ内に複数あります（{paths}）"

    # 3. 既存との関係（同じ種類のルート内）
    conflicts: list[Item] = []
    for it in items:
        if it.action:
            continue
        existing = repo.entry(it.kind, it.id)
        stray = None
        if existing is None:   # 索引にない（読み込めない）同名ファイル
            stray = next(iter(repo.root_dir(it.kind).rglob(f"{it.id}.yaml")), None)
        if existing is not None and existing.raw == cands[it.path].data:
            it.action = SKIP_SAME
            it.reason = f"すでに同じ内容があります（{repo.rel(existing.path)}）"
        elif existing is not None or stray is not None:
            it.conflict = True
            it.existing_path = existing.path if existing is not None else stray
            it.existing = repo.rel(it.existing_path)
            it.existing_sig = _sig(it.existing_path)
            conflicts.append(it)
        else:
            it.action, it.final_id = CREATE, it.id
    used: dict[str, set[str]] = {k: {i.final_id for i in items if i.action == CREATE and i.kind == k} for k in KINDS}
    for it in conflicts:
        it.suggested = _suggest(repo, it.kind, it.id, used)
        new = (choices.rename.get(it.path) or "").strip()
        wants_rename = it.path in choices.rename
        if it.path in choices.overwrite:
            if wants_rename:
                it.action, it.reason = REJECT, "「上書きする」と「別IDで取り込む」は同時に選べません。どちらか一方にしてください"
            else:
                it.action, it.final_id, it.dest = OVERWRITE, it.id, it.existing_path
                it.reason = f"既存のファイルを上書きします（{it.existing}）"
            continue
        if not new:
            it.action = SKIP_CHOICE
            it.reason = f"同じ id が既にあり、中身が違います（既存：{it.existing}）。スキップします（別 ID で取り込むこともできます）"
            continue
        bad = _check_new_id(repo, it.kind, new, used)
        if bad:
            it.action, it.reason = REJECT, f"別 ID '{new}' は使えません：{bad}"
        else:
            it.action, it.final_id = RENAMED, new
            used[it.kind].add(new)

    # 4. 検証（保存時と同じ規則）。バッチ内の相互参照を解決するため、取り込むオブジェクトを重ねた見え方で検査する。
    #    参照先が無いだけのエラーは警告にする。ほかのエラーで却下されたものは、見え方から外して検査し直す
    objs: dict[str, object] = {}
    for it in items:
        if it.writes:
            try:
                objs[it.path] = _BUILDERS[it.kind](cands[it.path].data)
            except Exception as e:  # noqa: BLE001
                it.action, it.reason = REJECT, f"オブジェクトに変換できません：{e}"
    issues_of: dict[str, list[Issue]] = {}
    while True:
        alive = [it for it in items if it.writes]
        ov = OverlayRepository(repo, {(it.kind, it.final_id): objs[it.path] for it in alive})
        again = False
        for it in alive:
            issues = _validate(ov, it.kind, objs[it.path])
            if it.action == OVERWRITE and it.kind == "action":   # 参照中の Action のスロットは、保存時と同じ規則で守る
                issues = issues + slot_issues(repo, it.id, list(objs[it.path].slots))
            issues_of[it.path] = issues
            errors = [i.message for i in issues if i.level == "error" and i.code != MISSING_REF]
            if errors:
                it.action, it.reason = REJECT, _short(errors)
                again = True
        if not again:
            break
    renamed = {(it.kind, it.id): it.final_id for it in items if it.action == RENAMED}
    for it in items:
        if not it.writes:
            continue
        if it.action == OVERWRITE:
            users = {"background": scenes_using_background, "action": scenes_using_action}.get(it.kind)
            used = users(repo, it.id) if users else []
            if used:
                it.warnings.append(f"上書きする{KIND_LABEL[it.kind]}は {len(used)} 件の Scene が使っています："
                                   + "、".join(e.id for e in used))
        for i in issues_of[it.path]:
            if i.level == "warning":
                it.warnings.append(i.message)
            elif i.code == MISSING_REF:
                it.warnings.append(f"参照先が見つかりません：{i.message}")
        for kind, rid in _refs(it.kind, objs[it.path]):
            if (kind, rid) in renamed:
                it.warnings.append(f"{KIND_LABEL[kind]} '{rid}' は別 ID（{renamed[(kind, rid)]}）で取り込まれます。"
                                   f"この参照は自動では書き換えず、既存の '{rid}' を指します")

    # 5. 配置先
    def base_dir(kind: str) -> Path:
        root = repo.root_dir(kind).resolve()
        rel = choices.dest.get(kind)
        if rel:
            p = (repo.root / rel).resolve()
            try:
                p.relative_to(root)
                return p
            except ValueError:
                pass
        return root

    for it in items:
        c = cands[it.path]
        it.by_choice = not (c.parts and c.parts[0] in ROOT_NAMES)
        if it.action == OVERWRITE:
            continue   # 書き込み先は既存のファイルそのもの（配置先の選択には依存しない）
        if it.writes:
            folder = repo.root.joinpath(*c.parts) if not it.by_choice else base_dir(it.kind).joinpath(*c.parts)
            it.dest = folder / f"{it.final_id}.yaml"
            if it.dest.exists():
                it.action, it.reason, it.dest = REJECT, "書き込み先に同名のファイルがあります", None
    plan.needs_dest = [k for k in KINDS if any(
        i.kind == k and i.by_choice and i.action in (CREATE, RENAMED, SKIP_CHOICE) for i in items)]

    # 6. フォルダ設定（_folder.yaml）
    def new_files_in(folder: Path) -> list[str]:
        return [f"{i.final_id}.yaml" for i in items if i.action in (CREATE, RENAMED) and i.dest is not None
                and i.dest.parent.resolve() == folder.resolve()]

    for c in insp.candidates:
        if not c.is_folder:
            continue
        if c.rejected:
            plan.folders.append(FolderItem(c.path, action=F_REJECT, reason=c.rejected))
            continue
        if c.parts and c.parts[0] in ROOT_NAMES:
            targets = [repo.root.joinpath(*c.parts)]
        else:   # 種類ごとに配置先を選ぶ場合：その相対フォルダの下に取り込むファイルがある種類の配置先に対して適用する
            kinds: list[str] = []
            for it in items:
                if (it.kind and it.by_choice and it.action not in (REJECT, SKIP_UNSELECTED) and it.kind not in kinds
                        and cands[it.path].parts[:len(c.parts)] == c.parts):
                    kinds.append(it.kind)
            targets = [base_dir(k).joinpath(*c.parts) for k in kinds]
        if not targets:
            plan.folders.append(FolderItem(c.path, action=F_REJECT,
                                           reason="このフォルダの下に取り込むファイルがありません（どの配置先に適用するか決められません）"))
        for folder in targets:
            plan.folders.append(_folder_item(repo, c, folder, choices, new_files_in(folder)))
    return plan


def _folder_item(repo: Repository, c: Candidate, folder: Path, choices: Choices, new_files: list[str]) -> FolderItem:
    key = repo.rel(folder)
    fi = FolderItem(c.path, folder, key=key, import_name=str(c.data.get("name") or ""))
    meta_file = folder / FOLDER_FILE
    if not folder.is_dir():   # 配置先にまだ無いフォルダ：そのまま書く
        fi.action, fi.text = F_CREATE, c.text
        return fi
    if not meta_file.exists():   # フォルダはあるが _folder.yaml が無い：表示名が変わるので選ばせる（初期値は付けない）
        fi.label_choice = True
        if choices.label.get(key):
            fi.action, fi.text = F_CREATE, c.text
        else:
            fi.action = F_SKIP_LABEL
            fi.reason = "既存のフォルダに _folder.yaml が無く、表示名が変わるため付けません（選択で付けることもできます）"
        return fi
    meta = repo.folder_meta.get(folder) or repo.folder_meta.get(folder.resolve())
    if meta is None:
        fi.action, fi.reason = F_REJECT, "既存の _folder.yaml を読めないため、取り込みません"
        return fi
    imp_name, ex_name = c.data.get("name"), meta.name
    if imp_name is not None and imp_name != ex_name:   # name が違う（既存に無い場合も）→ 却下。既存は上書きしない
        fi.action = F_REJECT
        fi.reason = (f"name が違います（既存：{ex_name if ex_name else '（なし）'}／取り込み側：{imp_name}）。"
                     "既存の _folder.yaml は上書きしないため、取り込みません")
        return fi
    imp_desc, ex_desc = c.data.get("description") or "", meta.description or ""
    desc = ex_desc
    if imp_desc and imp_desc != ex_desc:
        if not ex_desc:   # 既存が空なら、取り込み側を自動で使う
            desc = imp_desc
        else:
            fi.desc_conflict = (ex_desc, imp_desc)
            desc = imp_desc if choices.desc.get(key) == "import" else ex_desc
    order = list(meta.order)
    if order:   # 既存に order がある場合だけ、取り込んだファイルを末尾に足す
        order += [n for n in new_files if n not in order]
    merged = {"name": ex_name or "", "description": desc, "order": order}
    if (merged["name"], merged["description"], merged["order"]) == (ex_name or "", ex_desc, list(meta.order)):
        fi.action = F_SKIP_SAME
        fi.reason = "合成した結果が既存の _folder.yaml と同じになるため、書き込みません"
    else:
        fi.action, fi.merged = F_MERGE, merged
    return fi
