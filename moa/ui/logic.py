"""画面の状態遷移（10章）。Gradio に依存しない純粋関数（テスト可能）。

状態は3つの dict に分ける：
  sc … 組み立て中のSceneの内容。変わるとキャスト枠を描き直す
  nav … 共有ブラウザの選択先と表示フォルダ、保存先フォルダ。フォルダ移動だけでは枠を描き直さない
  gp … 画面で上書きした生成パラメータ（動かした項目だけ。保存で Scene.gen になる）
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from ruamel.yaml import YAML

from moa.comfy.generate import differences
from moa.core import CastEntry, Repository, Scene, compile_scene, resolve_outfit, save_scene, validate_scene_id
from moa.core.constants import ROOTS
from moa.core.gen import GEN_PARAMS, clean_gen, normalize_value
from moa.core.models import CompileResult, Issue, has_error
from moa.core.scene_io import GenerationRecord
from moa.core.tree import breadcrumb, list_folder

# 共有ブラウザの選択先 → ルートフォルダ。savedir は「保存先フォルダ」を scenes/ 配下から選ぶモード
SECTION = {"scene": "scenes", "action": "actions", "background": "backgrounds",
           "char": "characters", "outfit": "outfits", "savedir": "scenes"}
KIND = {"scene": "scene", "action": "action", "background": "background", "char": "character", "outfit": "outfit"}
INHERIT = "__inherit__"
BG_SOURCE = {"action": "Action の推奨背景", "user": "選んだ背景", "scene": "開いた Scene の背景"}


def new_sc() -> dict:
    return {"scene_id": None, "scene_name": "", "background_id": None, "bg_source": None, "action_id": None,
            "cast": {}, "apply_negative": False,
            "gen": {},        # 開いたSceneに保存されていた Scene.gen（操作中の値は gp 側）
            "open_seq": 0}    # Scene を開くたびに増える。gp が「開き直し」を検知するための番号


def new_nav() -> dict:
    # cwd はルート名をキーに、data からの相対パスを持つ。save_dir は新規 Scene の保存先
    return {"target": "scene", "slot": None, "cwd": {r: r for r in ROOTS}, "save_dir": "scenes"}


# ---- Scene の組み立て ----
def build_scene(sc: dict) -> Scene | None:
    if not sc["action_id"]:
        return None
    cast = {slot: CastEntry(e["character"], e["outfit"]) for slot, e in sc["cast"].items() if e["character"]}
    return Scene(id=sc["scene_id"] or "draft", background=sc["background_id"] or "", action=sc["action_id"],
                 cast=cast, name=sc["scene_name"] or None, apply_negative=sc["apply_negative"],
                 gen=copy.deepcopy(sc["gen"]))


def slots_of(repo: Repository, sc: dict) -> list[str]:
    action = repo.action(sc["action_id"]) if sc["action_id"] else None
    return list(action.slots) if action else []


def compile_view(repo: Repository, sc: dict) -> CompileResult | None:
    scene = build_scene(sc)
    if scene is None:
        return None
    if not sc["background_id"]:
        r = CompileResult(scene.id, "", sc["action_id"], sc["apply_negative"])
        r.issues = [Issue("error", "Background が未選択です。「Background を選ぶ」から選んでください")]
        return r
    return compile_scene(repo, scene)


# ---- 共有ブラウザ ----
def target_label(nav: dict) -> str:
    t, slot = nav["target"], nav["slot"]
    return {
        "scene": "**選択先：Scene（既存の Scene を開く）**",
        "action": "**選択先：Action（新規 Scene の元にする）**",
        "background": "**選択先：Background**",
        "char": f"**選択先：`{slot}` のキャラクター**",
        "outfit": f"**選択先：`{slot}` の服装**",
        "savedir": "**選択先：Scene の保存先フォルダ**（フォルダを開いて「このフォルダを保存先にする」）",
    }[t]


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


def save_dir_path(repo: Repository, nav: dict) -> Path:
    """保存先フォルダ（scenes/ 配下）。scenes/ の外は使わず、scenes/ に戻す。まだないフォルダは、保存時に作られる。"""
    root = (repo.root / "scenes").resolve()
    path = (repo.root / nav["save_dir"]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return root
    return path


def savedir_text(repo: Repository, nav: dict) -> str:
    path = save_dir_path(repo, nav)
    return f"新規 Scene の保存先：`{repo.rel(path)}/`"


def listing(repo: Repository, nav: dict) -> tuple[list[tuple[str, str]], str]:
    """(Radioの選択肢[(表示, 値)], パンくず文字列)。savedir のときはフォルダだけを並べる。"""
    root, cwd = _section_root(repo, nav), _cwd(repo, nav)
    choices: list[tuple[str, str]] = []
    if cwd != root:
        choices.append(("⬆ 上へ", "up"))
    for it in list_folder(repo, cwd):
        if nav["target"] == "savedir" and it.kind != "folder":
            continue
        prefix = "dir:" if it.kind == "folder" else "file:"
        choices.append((it.label, prefix + repo.rel(it.path)))
    crumbs = " / ".join(label for label, _ in breadcrumb(repo, root, cwd))
    return choices, crumbs


def _set_cwd(repo: Repository, nav: dict, folder: Path) -> dict:
    nav = copy.deepcopy(nav)
    nav["cwd"][SECTION[nav["target"]]] = repo.rel(folder)
    return nav


def choose_savedir(repo: Repository, nav: dict) -> tuple[dict, str]:
    """今開いているフォルダを、新規 Scene の保存先にする。"""
    if nav["target"] != "savedir":
        return nav, "⚠ 先に「保存先フォルダを選ぶ」を押して、保存先を選ぶ画面にしてください"
    nav = copy.deepcopy(nav)
    nav["save_dir"] = repo.rel(_cwd(repo, nav))
    return nav, f"✅ 保存先を `{nav['save_dir']}/` にしました"


def _first_empty_slot(repo: Repository, sc: dict) -> str | None:
    return next((s for s in slots_of(repo, sc) if not (sc["cast"].get(s) or {}).get("character")), None)


def _next_target(repo: Repository, sc: dict, nav: dict) -> dict:
    """続けてキャラを選べるよう、キャラ未選択の先頭スロット（なければ先頭）に選択先を切り替える。"""
    slots = slots_of(repo, sc)
    if slots:
        nav["target"] = "char"
        nav["slot"] = _first_empty_slot(repo, sc) or slots[0]
    return nav


def apply_entry(repo: Repository, sc: dict, nav: dict, entry) -> tuple[dict, dict, str]:
    """選択先に応じて、選んだエントリを反映する。(sc, nav, メッセージ)"""
    sc, nav = copy.deepcopy(sc), copy.deepcopy(nav)
    t = nav["target"]
    if t == "savedir":
        return sc, nav, "⚠ 保存先にはフォルダを選んでください（ファイルは選べません）"
    if entry.kind != KIND[t]:
        return sc, nav, f"⚠ ここでは {KIND[t]} を選んでください（{entry.kind}）"

    if t == "scene":
        s: Scene = entry.obj
        sc = {"scene_id": s.id, "scene_name": s.name or "", "background_id": s.background, "bg_source": "scene",
              "action_id": s.action,
              "cast": {slot: {"character": e.character, "outfit": e.outfit} for slot, e in s.cast.items()},
              "apply_negative": s.apply_negative, "gen": copy.deepcopy(s.gen), "open_seq": sc["open_seq"] + 1}
        return sc, _next_target(repo, sc, nav), f"✅ Scene を開きました：{s.name or s.id}"

    if t == "action":
        prev = sc
        sc = new_sc()
        sc["open_seq"] = prev["open_seq"]
        sc["action_id"] = entry.id
        sc["apply_negative"] = prev["apply_negative"]
        for slot in entry.obj.slots:                 # 選び直しでは、同名スロットのキャスト・服装を引き継ぐ
            if slot in prev["cast"]:
                sc["cast"][slot] = dict(prev["cast"][slot])
        # Background：手で選んだものが常に優先。そうでなければ Action の推奨背景を初期選択にする（なければ未選択）
        if prev["background_id"] and prev["bg_source"] == "user":
            sc["background_id"], sc["bg_source"] = prev["background_id"], "user"
        elif entry.obj.background and repo.background(entry.obj.background):
            sc["background_id"], sc["bg_source"] = entry.obj.background, "action"
        msg = f"✅ Action を選びました：{entry.obj.name}"
        if sc["bg_source"] == "action":
            msg += f"（推奨背景 `{sc['background_id']}` を選択）"
        elif not sc["background_id"]:
            msg += "（推奨背景がないので、Background を選んでください）"
        return sc, _next_target(repo, sc, nav), msg

    if t == "background":
        sc["background_id"], sc["bg_source"] = entry.id, "user"
        return sc, nav, f"✅ Background → {entry.obj.name}"

    slot = nav["slot"]
    cast = sc["cast"].setdefault(slot, {"character": None, "outfit": None})
    if t == "char":
        cast["character"] = entry.id
        return sc, nav, f"✅ {slot} → {entry.obj.name}"
    cast["outfit"] = entry.id
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
    """ブラウザ経由では元パスが取れないので、YAMLの id から索引で元ファイルを特定する（10章）。"""
    if not uploaded:
        return sc, nav, ""
    if nav["target"] == "savedir":
        return sc, nav, "⚠ 保存先にはフォルダを選んでください"
    try:
        data = YAML(typ="safe").load(Path(uploaded).read_text(encoding="utf-8"))
    except Exception as e:
        return sc, nav, f"⚠ YAMLを読み込めません：{e}"
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return sc, nav, "⚠ トップレベルに id がありません"

    t = nav["target"]
    entry = repo.entry(KIND[t], data["id"])
    if entry is None:
        return sc, nav, (f"⚠ id '{data['id']}' は {KIND[t]} の索引にありません。"
                         "data/ に置いて「再読み込み」してください")
    sc, nav, msg = apply_entry(repo, sc, nav, entry)
    nav["cwd"][SECTION[t]] = repo.rel(entry.path.parent)  # 元ファイルのあるフォルダを開いておく
    if data != entry.raw:
        msg += f"\n⚠ アップロードされた内容は data/ 側と異なります。data/ 側（{repo.rel(entry.path)}）を使用しました"
    return sc, nav, msg


# ---- キャスト枠の表示 ----
def inherit_label(repo: Repository, sc: dict, slot: str) -> str:
    action = repo.action(sc["action_id"]) if sc["action_id"] else None
    char = repo.character((sc["cast"].get(slot) or {}).get("character") or "")
    if action is None or char is None:
        return "未指定（継承）"
    res = resolve_outfit(slot, None, action.outfits, char.default_outfit)
    return {
        "action": f"未指定（アクション指定：{res.outfit_id}）",
        "none_action": "未指定（アクション指定：none）",
        "character": f"未指定（キャラ既定：{res.outfit_id}）",
        "none_character": "未指定（キャラ既定：none）",
    }.get(res.source, "未指定（服装なし）")


def outfit_choices(repo: Repository, sc: dict, slot: str) -> tuple[list[tuple[str, str]], str]:
    raw = (sc["cast"].get(slot) or {}).get("outfit")
    choices = [(inherit_label(repo, sc, slot), INHERIT), ("none（着せない）", "none")]
    if raw and raw != "none":
        o = repo.outfit(raw)
        choices.append((f"{o.name}（{raw}）" if o else f"{raw}（見つかりません）", raw))
    return choices, (raw or INHERIT)


def set_outfit(sc: dict, slot: str, value: str) -> dict:
    sc = copy.deepcopy(sc)
    sc["cast"].setdefault(slot, {"character": None, "outfit": None})["outfit"] = None if value == INHERIT else value
    return sc


def character_text(repo: Repository, sc: dict, slot: str) -> str:
    cid = (sc["cast"].get(slot) or {}).get("character")
    if not cid:
        return "_（未選択）_"
    entry = repo.entry("character", cid)
    if entry is None:
        return f"⚠ `{cid}` が見つかりません"
    sex = "" if entry.obj.sex == "female" else "（male）"
    return f"**{entry.obj.name}**{sex}  \n`{repo.rel(entry.path)}`"


# ---- コンパイル結果の表示 ----
def issues_text(issues) -> str:
    if not issues:
        return "✅ 問題なし"
    return "\n".join(f"- {'❌' if i.level == 'error' else '⚠️'} {i.message}"
                     + (f"（{i.path.name}）" if i.path else "") for i in issues)


def summary_text(r: CompileResult | None) -> str:
    """実効 rating・人数タグ・canvas（10章：確認できること）。"""
    if r is None or not r.ok:
        return ""
    return (f"**実効 rating：`{r.rating}`** ／ **人数タグ：`{r.people_tags or '（なし）'}`** ／ "
            f"canvas：{r.canvas['w']}×{r.canvas['h']}")


def selection_text(repo: Repository, sc: dict) -> str:
    action = repo.action(sc["action_id"]) if sc["action_id"] else None
    if action is None:
        return "_Scene を開くか、Action を選んでください。左の一覧から選べます。_"
    lines = [f"**Action：{action.name}**  \n`{repo.rel(repo.path_of('action', action.id))}`"]
    bg = repo.background(sc["background_id"]) if sc["background_id"] else None
    if bg is not None:
        src = BG_SOURCE.get(sc["bg_source"], "")
        lines.append(f"**Background：{bg.name}**{f'（{src}）' if src else ''}  \n`{repo.rel(repo.path_of('background', bg.id))}`"
                     f"（{bg.canvas['w']}×{bg.canvas['h']} / {bg.rating}）")
    elif sc["background_id"]:
        lines.append(f"⚠ Background `{sc['background_id']}` が見つかりません")
    else:
        lines.append("**Background：未選択**（「Background を選ぶ」から選んでください）")
    if sc["scene_id"]:
        p = repo.path_of("scene", sc["scene_id"])
        lines.append(f"**開いているScene：{sc['scene_name'] or sc['scene_id']}**  \n`{repo.rel(p) if p else '（未保存）'}`")
    return "\n\n".join(lines)


# ---- 保存（10章）----
def auto_scene_id(repo: Repository, sc: dict) -> str:
    """id が空のときの自動命名：`<名前>_1`, `_2`…（空いている最小の番号）。

    名前の元は、開いているSceneの id（末尾の `_数字` は除く）。なければ `<action id>__scene`。
    """
    base = re.sub(r"_\d+$", "", sc["scene_id"]) if sc["scene_id"] else f"{sc['action_id']}__scene"

    def taken(sid: str) -> bool:  # 索引にあるか、索引にない（読み込めない）ファイルが同名で存在するか
        return repo.entry("scene", sid) is not None or any(repo.root_dir("scene").rglob(f"{sid}.yaml"))

    n = 1
    while taken(f"{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def save(repo: Repository, sc: dict, nav: dict, scene_id: str, display_name: str, generated: dict | None = None,
         gen_params: dict | None = None, effective: dict | None = None) -> tuple[dict, str]:
    """Scene を保存する。id が空なら `<名前>_N` で新規保存。既存 Scene の上書きは、同じ id を明示したときだけ。

    generated（生成結果のスナップショット）は、生成時と今で食い違う項目がなければ seed / images / gen / partial として記録する。
    食い違うときは、記録せず、違った項目名を返すメッセージに列挙する。
    gen_params は画面で上書きした項目（gp["gen"]）で、Scene.gen として保存する。
    effective は gen の実効値（記録条件の比較用。ComfyUI 連携が無効なら None）。
    """
    sc = copy.deepcopy(sc)
    if not sc["action_id"]:
        return sc, "⚠ Action が未選択です"
    if not sc["background_id"]:
        return sc, "⚠ Background が未選択です"
    if display_name.strip():
        sc["scene_name"] = display_name.strip()
    if gen_params is not None:
        sc["gen"] = clean_gen(gen_params)
    try:
        scene_id = scene_id.strip() or auto_scene_id(repo, sc)
        sc["scene_id"] = validate_scene_id(scene_id)
        overwrite = repo.scene(sc["scene_id"]) is not None
        scene = build_scene(sc)
        result = compile_scene(repo, scene)
        if has_error(result.issues):
            return sc, "❌ 検証エラーがあるため保存できません"

        record, note = None, ""
        if generated and generated.get("images"):
            diff = differences(generated, result, effective)
            if diff:
                note = ("\n\nℹ 生成後に次の項目が変わったため、生成結果（シード・画像）は記録していません"
                        + ("（部分結果も破棄）" if generated.get("partial") else "") + "：" + "、".join(diff))
            else:
                record = GenerationRecord(generated["seed"], list(generated["images"]), generated.get("gen"),
                                          partial=bool(generated.get("partial")))
                note = (f"\n\n🖼 シード {record.seed} と画像 {len(record.images)} 枚のパスを記録しました"
                        + ("（MOSS段だけの部分結果。`compiled.partial: true`）" if record.partial else ""))
        path = save_scene(repo, scene, result, generation=record,
                          folder=None if overwrite else save_dir_path(repo, nav))
    except ValueError as e:
        return sc, f"⚠ {e}"
    repo.reload()
    verb = "上書き保存しました" if overwrite else "新規保存しました"
    return sc, f"💾 {verb}：`{path}`{note}"


# ---- 生成パラメータ（Scene.gen）----
# gp = {"seq": open_seq, "gen": {段: {項目: 値}}}  … 画面で上書きした項目だけを持つ（動かした項目だけ Scene.gen に保存）
PARAM_LABEL = {"steps": "steps", "cfg": "cfg", "denoise": "denoise"}
ITEMS = [(stage, param) for stage, params in GEN_PARAMS.items() for param in params]
SOURCE_LABEL = {"scene": "Scene", "workflow": "ワークフロー"}


def new_gp() -> dict:
    return {"seq": 0, "gen": {}}


def sync_gp(sc: dict, gp: dict) -> dict:
    """Scene を（開き直しも含めて）開いたら、その Scene.gen を操作中の値にする。

    Action / Background を選び直しただけのときは番号が変わらないので、操作中の値を保つ。
    """
    if sc["open_seq"] != gp["seq"]:
        return {"seq": sc["open_seq"], "gen": clean_gen(sc["gen"])}
    return gp


def set_override(gp: dict, stage: str, param: str, value) -> dict:
    gp = copy.deepcopy(gp)
    gp["gen"].setdefault(stage, {})[param] = normalize_value(param, value)
    gp["gen"] = clean_gen(gp["gen"])
    return gp


def clear_override(gp: dict, stage: str, param: str) -> dict:
    """その項目を Scene.gen から削除する（ワークフローの値に戻す）。"""
    gp = copy.deepcopy(gp)
    gp["gen"].get(stage, {}).pop(param, None)
    gp["gen"] = clean_gen(gp["gen"])
    return gp


def param_state(cfg, gp: dict, stage: str, param: str) -> dict:
    """スライダーに出す値・出どころ（Scene / ワークフロー）・上書き中か・操作できるか。"""
    if cfg is None or not cfg.has_sampler(stage):
        return {"value": None, "source": None, "overridden": False, "enabled": False}
    over = gp["gen"].get(stage, {}).get(param)
    if over is not None:
        return {"value": over, "source": "scene", "overridden": True, "enabled": True}
    return {"value": cfg.workflow_value(stage, param), "source": "workflow", "overridden": False, "enabled": True}


def source_text(state: dict) -> str:
    if state["source"] is None:
        return "_（未設定）_"
    return f"出どころ：**{SOURCE_LABEL[state['source']]}**"


def gen_summary(gen: dict) -> str:
    """生成時に使った値の一覧（継承分も含む）。"""
    labels = {"moss": "MOSS段", "anima": "Anima段"}
    return " ／ ".join(f"{labels[s]}：" + ", ".join(f"{p} {v}" for p, v in params.items())
                       for s, params in gen.items() if params)
