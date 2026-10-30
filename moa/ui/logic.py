"""画面の状態遷移。Gradio に依存しない純粋関数（テスト可能）。

状態は2つの dict に分ける：
  sc  … 組み立て中のSceneの内容。変わるとキャスト枠を描き直す
  nav … 共有ブラウザの選択先と表示フォルダ。フォルダ移動だけでは枠を描き直さない
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from ruamel.yaml import YAML

from moa.core import Repository, Scene, compile_scene, new_scene_id, resolve_outfit, save_scene
from moa.core.models import has_error
from moa.core.scene_io import GenerationRecord
from moa.core.repository import is_scene_dict
from moa.core.tree import breadcrumb, list_folder

SECTION = {"situation": "situations", "char": "characters", "outfit": "outfits"}
INHERIT = "__inherit__"


def new_sc() -> dict:
    return {"situation_id": None, "scene_id": None, "scene_name": "",
            "cast": {}, "outfits": {}, "apply_negative": False}


def new_nav() -> dict:
    # cwd はセクション名（situations / characters / outfits）をキーに、data からの相対パスを持つ
    return {"target": "situation", "slot": None, "cwd": {s: s for s in SECTION.values()}}


# ---- Scene の組み立て ----
def build_scene(sc: dict) -> Scene | None:
    if not sc["situation_id"]:
        return None
    sid = sc["scene_id"] or f"{sc['situation_id']}__draft"
    return Scene(id=sid, situation=sc["situation_id"], cast=dict(sc["cast"]),
                 name=sc["scene_name"] or None, outfits=dict(sc["outfits"]),
                 apply_negative=sc["apply_negative"])


def slots_of(repo: Repository, sc: dict) -> list[str]:
    sit = repo.situation(sc["situation_id"]) if sc["situation_id"] else None
    return list(sit.slots) if sit else []


# ---- 共有ブラウザ ----
def target_label(nav: dict) -> str:
    t, slot = nav["target"], nav["slot"]
    if t == "char":
        return f"**選択先：`{slot}` のキャラクター**"
    if t == "outfit":
        return f"**選択先：`{slot}` の服装**"
    return "**選択先：シチュエーション（Situation を選ぶと新規、Scene を選ぶと既存を開く）**"


def set_target(nav: dict, target: str, slot: str | None = None) -> dict:
    nav = copy.deepcopy(nav)
    nav["target"], nav["slot"] = target, slot
    return nav


def _section_root(repo: Repository, nav: dict) -> Path:
    return repo.root / SECTION[nav["target"]]


def _cwd(repo: Repository, nav: dict) -> Path:
    """表示フォルダ。範囲外・消えたフォルダはセクションのルートに戻す。"""
    root = _section_root(repo, nav)
    cwd = (repo.root / nav["cwd"][SECTION[nav["target"]]]).resolve()
    try:
        cwd.relative_to(root)
    except ValueError:
        return root
    return cwd if cwd.is_dir() else root


def listing(repo: Repository, nav: dict) -> tuple[list[tuple[str, str]], str]:
    """(Radioの選択肢[(表示, 値)], パンくず文字列)"""
    root, cwd = _section_root(repo, nav), _cwd(repo, nav)
    choices: list[tuple[str, str]] = []
    if cwd != root:
        choices.append(("⬆ 上へ", "up"))
    for it in list_folder(repo, cwd):
        prefix = "dir:" if it.kind == "folder" else "file:"
        choices.append((it.label, prefix + repo.rel(it.path)))
    crumbs = " / ".join(label for label, _ in breadcrumb(repo, root, cwd))
    return choices, crumbs


def _set_cwd(repo: Repository, nav: dict, folder: Path) -> dict:
    nav = copy.deepcopy(nav)
    nav["cwd"][SECTION[nav["target"]]] = repo.rel(folder)
    return nav


def apply_entry(repo: Repository, sc: dict, nav: dict, entry) -> tuple[dict, dict, str]:
    """選択先に応じて、選んだエントリを反映する。(sc, nav, メッセージ)"""
    sc, nav = copy.deepcopy(sc), copy.deepcopy(nav)
    t = nav["target"]
    if t == "situation":
        if entry.kind == "situation":
            prev, sc = sc, new_sc()
            sc["situation_id"] = entry.id
            # 選び直しでは、新しいSituationにも存在するスロットのキャスト・服装を引き継ぐ
            for slot in entry.obj.slots:
                if slot in prev["cast"]:
                    sc["cast"][slot] = prev["cast"][slot]
                if slot in prev["outfits"]:
                    sc["outfits"][slot] = prev["outfits"][slot]
        elif entry.kind == "scene":
            s: Scene = entry.obj
            sc = {"situation_id": s.situation, "scene_id": s.id, "scene_name": s.name or "",
                  "cast": dict(s.cast), "outfits": dict(s.outfits),
                  "apply_negative": s.apply_negative}
        else:
            return sc, nav, f"⚠ ここでは Situation か Scene を選んでください（{entry.kind}）"
        slots = slots_of(repo, sc)
        if slots:  # 続けてキャラを選べるよう、キャラ未選択の先頭スロット（なければ先頭）に切り替える
            nav["target"] = "char"
            nav["slot"] = next((s for s in slots if s not in sc["cast"]), slots[0])
        return sc, nav, f"✅ {entry.kind} を読み込みました：{entry.obj.name or entry.id}"
    slot = nav["slot"]
    if t == "char":
        if entry.kind != "character":
            return sc, nav, f"⚠ キャラクターを選んでください（{entry.kind}）"
        sc["cast"][slot] = entry.id
        return sc, nav, f"✅ {slot} → {entry.obj.name}"
    if entry.kind != "outfit":
        return sc, nav, f"⚠ 服装を選んでください（{entry.kind}）"
    sc["outfits"][slot] = entry.id
    return sc, nav, f"✅ {slot} の服装 → {entry.obj.name}"


def browse(repo: Repository, sc: dict, nav: dict, choice: str | None) -> tuple[dict, dict, str]:
    """Radio で項目を選んだとき。"""
    if not choice:
        return sc, nav, ""
    cwd = _cwd(repo, nav)
    if choice == "up":
        return sc, _set_cwd(repo, nav, cwd.parent), ""
    kind, _, rel = choice.partition(":")
    path = (repo.root / rel).resolve()
    if kind == "dir":
        return sc, _set_cwd(repo, nav, path), ""
    entry = repo.by_path.get(path)
    if entry is None:
        return sc, nav, f"⚠ {rel} は索引にありません（再読み込みしてください）"
    return apply_entry(repo, sc, nav, entry)


def from_upload(repo: Repository, sc: dict, nav: dict, uploaded: str | None) -> tuple[dict, dict, str]:
    """ブラウザ経由では元パスが取れないので、YAMLの id から索引で元ファイルを特定する。"""
    if not uploaded:
        return sc, nav, ""
    try:
        data = YAML(typ="safe").load(Path(uploaded).read_text(encoding="utf-8"))
    except Exception as e:
        return sc, nav, f"⚠ YAMLを読み込めません：{e}"
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return sc, nav, "⚠ トップレベルに id がありません"

    t = nav["target"]
    kind = {"char": "character", "outfit": "outfit"}.get(t) or ("scene" if is_scene_dict(data) else "situation")
    entry = repo.entry(kind, data["id"])
    if entry is None:
        return sc, nav, (f"⚠ id '{data['id']}' は {kind} の索引にありません。"
                         "data/ に置いて「再読み込み」してください")
    sc, nav, msg = apply_entry(repo, sc, nav, entry)
    nav["cwd"][SECTION[t]] = repo.rel(entry.path.parent)  # 元ファイルのあるフォルダを開いておく
    if data != entry.raw:
        msg += f"\n⚠ アップロードされた内容は data/ 側と異なります。data/ 側（{repo.rel(entry.path)}）を使用しました"
    return sc, nav, msg


# ---- キャスト枠の表示 ----
def inherit_label(repo: Repository, sc: dict, slot: str) -> str:
    sit = repo.situation(sc["situation_id"])
    char = repo.character(sc["cast"].get(slot, ""))
    if sit is None or char is None:
        return "未指定（継承）"
    res = resolve_outfit(slot, {}, sit.outfits, char.default_outfit)
    return {
        "situation": f"未指定（シチュエーション指定：{res.outfit_id}）",
        "none_situation": "未指定（シチュエーション指定：none）",
        "character": f"未指定（キャラのdefaultOutfit：{res.outfit_id}）",
        "none_character": "未指定（キャラのdefaultOutfit：none）",
    }.get(res.source, "未指定（服装なし）")


def outfit_choices(repo: Repository, sc: dict, slot: str) -> tuple[list[tuple[str, str]], str]:
    raw = sc["outfits"].get(slot)
    choices = [(inherit_label(repo, sc, slot), INHERIT), ("none（着せない）", "none")]
    if raw and raw != "none":
        o = repo.outfit(raw)
        choices.append((f"{o.name}（{raw}）" if o else f"{raw}（見つかりません）", raw))
    return choices, (raw or INHERIT)


def set_outfit(sc: dict, slot: str, value: str) -> dict:
    sc = copy.deepcopy(sc)
    sc["outfits"][slot] = None if value == INHERIT else value
    return sc


def character_text(repo: Repository, sc: dict, slot: str) -> str:
    cid = sc["cast"].get(slot)
    if not cid:
        return "_（未選択）_"
    entry = repo.entry("character", cid)
    if entry is None:
        return f"⚠ `{cid}` が見つかりません"
    return f"**{entry.obj.name}**  \n`{repo.rel(entry.path)}`"


# ---- コンパイル結果の表示 ----
def issues_text(issues) -> str:
    if not issues:
        return "✅ 問題なし"
    return "\n".join(f"- {'❌' if i.level == 'error' else '⚠️'} {i.message}"
                     + (f"（{i.path.name}）" if i.path else "") for i in issues)


def selection_text(repo: Repository, sc: dict) -> str:
    sit = repo.situation(sc["situation_id"]) if sc["situation_id"] else None
    if sit is None:
        return "_Situation / Scene が未選択です。左の一覧から選んでください。_"
    lines = [f"**Situation：{sit.name}**  \n`{repo.rel(repo.path_of('situation', sit.id))}`  "
             f"（{sit.canvas['w']}×{sit.canvas['h']} / {sit.rating}）"]
    if sc["scene_id"]:
        p = repo.path_of("scene", sc["scene_id"])
        lines.append(f"**開いているScene：{sc['scene_name'] or sc['scene_id']}**  \n"
                     f"`{repo.rel(p) if p else '（未保存）'}`")
    return "\n\n".join(lines)


def compile_view(repo: Repository, sc: dict):
    scene = build_scene(sc)
    if scene is None:
        return None
    return compile_scene(repo, scene)


def next_scene_slug(repo: Repository, sc: dict) -> str:
    """Scene名が空のときの自動命名：`<名前>_1`, `_2`…（空いている最小の番号）。

    名前の元は、開いているSceneのScene名（末尾の `_数字` は除く）。なければ "scene"。
    """
    sit = sc["situation_id"]
    base = "scene"
    if sc["scene_id"] and sc["scene_id"].startswith(f"{sit}__"):
        stem = re.sub(r"_\d+$", "", sc["scene_id"][len(sit) + 2:])
        base = stem or base
    n = 1
    while repo.path_of_any(f"{sit}__{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def generation_matches(generated: dict | None, result) -> bool:
    """生成結果が、今のコンパイル結果から作られたものか。"""
    return bool(generated) and (
        generated["moss_prompt"] == result.moss_prompt
        and generated["anima_prompt"] == result.anima_prompt
        and generated["negative_applied"] == result.apply_negative)


def save(repo: Repository, sc: dict, slug: str, display_name: str,
         generated: dict | None = None) -> tuple[dict, str]:
    """Scene名が空なら `<名前>_N` で新規保存。既存Sceneの上書きは、同じScene名を明示したときだけ。

    generated（生成結果）は、今の内容と一致するときだけ seed / images として記録する。
    """
    sc = copy.deepcopy(sc)
    if not sc["situation_id"]:
        return sc, "⚠ Situation が未選択です"
    if display_name.strip():
        sc["scene_name"] = display_name.strip()
    try:
        slug = slug.strip() or next_scene_slug(repo, sc)
        sc["scene_id"] = new_scene_id(sc["situation_id"], slug)
        overwrite = repo.scene(sc["scene_id"]) is not None
        if not overwrite and repo.path_of_any(sc["scene_id"]):
            return sc, f"⚠ id `{sc['scene_id']}` は別の種類のファイルで使われています"
        scene = build_scene(sc)
        result = compile_scene(repo, scene)
        if has_error(result.issues):
            return sc, "❌ 検証エラーがあるため保存できません"
        record = None
        if generation_matches(generated, result):
            record = GenerationRecord(generated["seed"], list(generated["images"]))
        path = save_scene(repo, scene, result, generation=record)
    except ValueError as e:
        return sc, f"⚠ {e}"
    repo.reload()
    verb = "上書き保存しました" if overwrite else "新規保存しました"
    msg = f"💾 {verb}：`{repo.rel(path)}`"
    if record:
        msg += f"\n\n🖼 シード {record.seed} と画像 {len(record.images)} 枚のパスを記録しました"
    elif generated:
        msg += "\n\nℹ 生成後に内容が変わったため、生成結果（シード・画像）は記録していません"
    return sc, msg
