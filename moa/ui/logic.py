"""画面の状態遷移（10章）。Gradio に依存しない純粋関数（テスト可能）。

状態は3つの dict に分ける：
  sc … 組み立て中のSceneの内容。変わるとキャスト枠を描き直す
  nav … 共有ブラウザの選択先と表示フォルダ、保存先フォルダ。フォルダ移動だけでは枠を描き直さない
  gp … 画面で上書きした生成パラメータ（動かした項目だけ。保存で Scene.gen になる）
  sd … シードの欄と固定の状態（固定中だけ、欄の値で生成する）
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from moa.comfy.generate import differences
from moa.comfy.workflow import SMALL_SIDE, anima_size
from moa.core import CastEntry, Repository, Scene, compile_scene, resolve_outfit, save_scene, validate_scene_id
from moa.core.constants import ROOTS, SEED_MAX
from moa.core.filestamp import ExternalChangeError
from moa.core.gen import GEN_PARAMS, clean_gen, normalize_value
from moa.core.models import CompileResult, Issue, has_error
from moa.core.scene_io import GenerationRecord
from moa.core import picker
from moa.core.tree import breadcrumb, list_folder

# 共有ブラウザの選択先 → ルートフォルダ（保存先は共有ブラウザでは選ばない。moa.ui.saveblock の保存ブロックで選ぶ）
SECTION = {"scene": "scenes", "action": "actions", "background": "backgrounds",
           "char": "characters", "outfit": "outfits"}
KIND = {"scene": "scene", "action": "action", "background": "background", "char": "character", "outfit": "outfit"}
INHERIT = "__inherit__"
EXTERNAL_PREFIX = "⚠ ファイルが外部で変更されています"   # save() がこの文言で始まるメッセージを返したら、2択を出す
BG_SOURCE = {"action": "Action の推奨背景", "user": "選んだ背景", "scene": "開いた Scene の背景"}


def new_sc() -> dict:
    return {"scene_id": None, "scene_name": "", "background_id": None, "bg_source": None, "action_id": None,
            "cast": {}, "apply_negative": False,
            "stamp": None,    # 開いたSceneのファイルの更新情報（外部変更の検知用。EDITOR_SPEC 12章-4）
            "gen": {},        # 開いたSceneに保存されていた Scene.gen（操作中の値は gp 側）
            "seed": None,     # 開いたSceneに compiled.seed として保存されていたシード（範囲外は None。操作中の値は sd 側）
            "open_seq": 0}    # Scene を開くたびに増える。gp が「開き直し」を検知するための番号


def new_nav() -> dict:
    # cwd はルート名をキーに、data からの相対パスを持つ
    return {"target": "scene", "slot": None, "cwd": {r: r for r in ROOTS}, "query": ""}


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
    }[t]


def set_target(nav: dict, target: str, slot: str | None = None) -> dict:
    nav = copy.deepcopy(nav)
    nav["target"], nav["slot"] = target, slot
    nav["query"] = ""
    return nav


def set_query(nav: dict, query: str | None) -> dict:
    nav = copy.deepcopy(nav)
    nav["query"] = (query or "").strip()
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
    """(Radioの選択肢[(表示, 値)], パンくず文字列)。"""
    root, cwd = _section_root(repo, nav), _cwd(repo, nav)
    choices: list[tuple[str, str]] = []
    query = nav.get("query") or ""
    if query:  # 検索中：ルート全体から平らに。クリックで選べる（値は file:<相対パス>）
        rows, total = picker.search(repo, root, query)
        for entry, where in rows:
            choices.append((f"{picker.entity_label(entry)}　📁 {where}", "file:" + repo.rel(entry.path)))
        return choices, f"🔍 検索：{query}（{picker.search_message(len(rows), total)}）"
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


def reopen_scene(repo: Repository, sc: dict, nav: dict) -> tuple[dict, dict, str]:
    """外部変更の確認で［再読み込み（編集を破棄）］を選んだとき：データを読み直し、開いている Scene を開き直す。"""
    repo.reload()
    entry = repo.entry("scene", sc["scene_id"]) if sc["scene_id"] else None
    if entry is None:
        return sc, nav, "⚠ 開いている Scene が見つかりません"
    nav = set_target(nav, "scene")
    return apply_entry(repo, sc, nav, entry)


def _first_empty_slot(repo: Repository, sc: dict) -> str | None:
    return next((s for s in slots_of(repo, sc) if not (sc["cast"].get(s) or {}).get("character")), None)


def _next_target(repo: Repository, sc: dict, nav: dict) -> dict:
    """続けてキャラを選べるよう、キャラ未選択の先頭スロット（なければ先頭）に選択先を切り替える。"""
    slots = slots_of(repo, sc)
    if slots:
        nav["target"] = "char"
        nav["slot"] = _first_empty_slot(repo, sc) or slots[0]
        nav["query"] = ""
    return nav


def apply_entry(repo: Repository, sc: dict, nav: dict, entry) -> tuple[dict, dict, str]:
    """選択先に応じて、選んだエントリを反映する。(sc, nav, メッセージ)"""
    sc, nav = copy.deepcopy(sc), copy.deepcopy(nav)
    t = nav["target"]
    if entry.kind != KIND[t]:
        return sc, nav, f"⚠ ここでは {KIND[t]} を選んでください（{entry.kind}）"

    if t == "scene":
        s: Scene = entry.obj
        sc = {"scene_id": s.id, "scene_name": s.name or "", "background_id": s.background, "bg_source": "scene",
              "action_id": s.action,
              "cast": {slot: {"character": e.character, "outfit": e.outfit} for slot, e in s.cast.items()},
              "apply_negative": s.apply_negative, "gen": copy.deepcopy(s.gen), "open_seq": sc["open_seq"] + 1,
              "seed": valid_seed((entry.raw.get("compiled") or {}).get("seed")),
              "stamp": list(entry.stamp) if entry.stamp else None}
        return sc, _next_target(repo, sc, nav), f"✅ Scene を開きました：{s.name or s.id}" + stale_note(repo, entry)

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


# ---- マスターデータ変更の表示（EDITOR_SPEC 12章-1）----
def stale_note(repo: Repository, entry) -> str:
    """保存済みの compiled.mossPrompt / animaPrompt と、今コンパイルした結果が違えば、その旨と違いの中身を返す。

    compiled にプロンプトが無い Scene、今コンパイルできない Scene（エラーあり）では何も返さない。
    """
    compiled = entry.raw.get("compiled") or {}
    saved = {"MOSS": compiled.get("mossPrompt"), "Anima": compiled.get("animaPrompt")}
    if all(v is None for v in saved.values()):
        return ""
    result = compile_scene(repo, entry.obj)
    if not result.ok:
        return ""
    now = {"MOSS": result.moss_prompt, "Anima": result.anima_prompt}
    diff = [k for k in saved if saved[k] is not None and str(saved[k]).strip() != now[k].strip()]
    if not diff:
        return ""
    return ("\n⚠ 保存時から内容が変わっています（キャラ・服装・背景・行為の編集など）："
            + " / ".join(diff) + " のプロンプトが違います")


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


# ---- 保存（10.2章）----
@dataclass(frozen=True)
class SaveTarget:
    """保存の行き先。path があれば既存のファイルへの上書き、folder があれば新しいファイルを作る（scenes/ 配下）。"""
    scene_id: str
    path: Path | None = None
    folder: Path | None = None


def save(repo: Repository, sc: dict, target: SaveTarget, display_name: str = "", generated: dict | None = None,
         gen_params: dict | None = None, effective: dict | None = None, force: bool = False) -> tuple[dict, str]:
    """Scene を target に保存し、保存した Scene を「開いている Scene」にして返す。

    compiled のスナップショットは毎回書く。generated（生成結果のスナップショット）は、生成時と今で食い違う項目がなければ
    seed / images / gen / partial として記録する。食い違うときは、記録せずに保存し、違った項目名を警告として返すメッセージに列挙する。
    クイック編集の内容は含めない（sc は編集前のコンパイル結果の元になる Scene の定義だけ）。
    gen_params は画面で上書きした項目（gp["gen"]）で、Scene.gen として保存する。
    effective は gen の実効値（記録条件の比較用。ComfyUI 連携が無効なら None）。
    既存のファイルを書くとき、読み込み後にファイルが外部で変更されていれば保存を止める（EXTERNAL_PREFIX で始まるメッセージ）。
    force=True はその確認で［上書き保存］を選んだとき。
    """
    sc = copy.deepcopy(sc)
    opened_id = sc["scene_id"]   # 開いている Scene の id（scene_id を書き換える前）
    if not sc["action_id"]:
        return sc, "⚠ Action が未選択です"
    if not sc["background_id"]:
        return sc, "⚠ Background が未選択です"
    if display_name.strip():
        sc["scene_name"] = display_name.strip()
    if gen_params is not None:
        sc["gen"] = clean_gen(gen_params)
    try:
        sc["scene_id"] = validate_scene_id(target.scene_id)
        scene = build_scene(sc)
        result = compile_scene(repo, scene)
        if has_error(result.issues):
            return sc, "❌ 検証エラーがあるため保存できません"

        record, note = None, ""
        if generated and generated.get("images") and generated.get("quick_edit"):   # 10.1章：試し打ちは記録しない
            note = ("\n\n⚠ クイック編集した文字列での生成（試し打ち）なので、シードと画像は記録していません"
                    "（Scene と compiled は編集内容を含まないコンパイル結果のまま保存しました）")
        elif generated and generated.get("images"):
            diff = differences(generated, result, effective)
            if diff:
                note = ("\n\n⚠ 生成後に次の項目が変わったため、生成結果（シード・画像）は記録していません"
                        + ("（部分結果も破棄）" if generated.get("partial") else "") + "：" + "、".join(diff))
            else:
                record = GenerationRecord(generated["seed"], list(generated["images"]), generated.get("gen"),
                                          partial=bool(generated.get("partial")))
                note = (f"\n\n🖼 シード {record.seed} と画像 {len(record.images)} 枚のパスを記録しました"
                        + ("（MOSS段だけの部分結果。`compiled.partial: true`）" if record.partial else ""))
        expected = None
        if target.path is not None and not force:
            entry = repo.entry("scene", sc["scene_id"])
            expected = tuple(sc["stamp"]) if opened_id == sc["scene_id"] and sc.get("stamp") else (entry.stamp if entry else None)
        path = save_scene(repo, scene, result, path=target.path, generation=record, folder=target.folder,
                          expected_stamp=expected)
    except ExternalChangeError:
        return sc, EXTERNAL_PREFIX + "（VS Code・エディタ・エクスプローラーなど）。読み直すか、上書きするか選んでください"
    except ValueError as e:
        return sc, f"⚠ {e}"
    repo.reload()
    entry = repo.entry("scene", sc["scene_id"])
    sc["stamp"] = list(entry.stamp) if entry and entry.stamp else None
    return sc, f"💾 保存しました：`{path}`{note}"


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


# ---- シード固定（SPEC 13.2）----
# sd = {"seq": open_seq, "fixed": bool, "value": int | None}
#   fixed … True の間は、欄の値で生成する。False のときの value は表示用（直前に使ったシード）で、生成にはランダムな値を使う
def valid_seed(value) -> int | None:
    """整数で 0〜SEED_MAX のときだけその値。それ以外（None・bool・小数・範囲外）は None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and 0 <= value <= SEED_MAX:
        return value
    return None


def new_sd() -> dict:
    return {"seq": 0, "fixed": False, "value": None}


def sync_sd(sc: dict, sd: dict) -> dict:
    """Scene を（開き直しも含めて）開いたら、保存されていたシードがあればそれで固定ON、なければランダムに戻す。

    Action / Background を選び直しただけのときは番号が変わらないので、今の状態を保つ。
    """
    if sc["open_seq"] != sd["seq"]:
        seed = valid_seed(sc.get("seed"))
        return {"seq": sc["open_seq"], "fixed": seed is not None, "value": seed}
    return sd


def toggle_sd(sd: dict, typed) -> tuple[dict, str]:
    """［シード固定］ボタン。固定中なら解除（ランダムに戻る）。解除中なら欄の値で固定する。(sd, 警告。なければ空)"""
    if sd["fixed"]:
        return {**sd, "fixed": False}, ""
    seed = valid_seed(typed)
    if seed is None:
        return sd, (f"⚠ 固定するシードを 0〜{SEED_MAX} の整数で入力してください" if typed is not None
                    else "⚠ 固定するシードが空です。先に一度生成するか、数値を入力してください")
    return {**sd, "fixed": True, "value": seed}, ""


def seed_for_run(sd: dict, typed) -> int | None:
    """生成に渡すシード。固定ON なら欄の値、OFF なら None（ランダム）。固定ON で欄が不正なら ValueError。"""
    if not sd["fixed"]:
        return None
    seed = valid_seed(typed)
    if seed is None:
        raise ValueError(f"固定するシードが範囲外か空です（0〜{SEED_MAX} の整数を入れてください）")
    return seed


def after_generate(sd: dict, snapshot: dict | None) -> dict:
    """生成が終わったら、固定OFF のときだけ、直前に使ったシードを欄に出す。

    snapshot は Generation.snapshot()（gen 状態）。MOSS段が失敗したとき（画像なし・None）は更新しない。
    """
    if sd["fixed"] or not snapshot or not snapshot.get("images"):
        return sd
    return {**sd, "value": snapshot["seed"]}


def seed_labels(sd: dict) -> tuple[str, str]:
    """(ボタンの文言, 状態の表示)。"""
    if sd["fixed"]:
        return "🔓 シード固定を解除", "🔒 **シード固定中**：欄の値で生成します"
    return "🔒 シードを固定", "🎲 **ランダム**：生成ごとに新しいシードを使います（欄は直前のシード）"


def seed_btn_classes(sd: dict) -> list[str]:
    """［シード固定］ボタンの CSS クラス。ランダム中（［シードを固定］が押せる状態）だけ moa-seed-fixed（オレンジ）を付ける。"""
    return [] if sd["fixed"] else ["moa-seed-fixed"]


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


def scale_text(canvas: dict | None, scale: float, usable: bool) -> str:
    """Anima段の解像度倍率をかけた結果のサイズ表示（画面だけの設定。Scene には保存しない）。"""
    if not usable:
        return "_（この設定では使えません：config.yaml の width / height と、MOSS→Anima の接続が必要です）_"
    if not canvas:
        return "_（Action を選ぶとサイズを表示します）_"
    cw, ch = canvas["w"], canvas["h"]
    if scale == 1.0:
        return f"→ canvas のまま（{cw}×{ch}）"
    w, h = anima_size(canvas, scale)
    warn = f"　⚠ {SMALL_SIDE}px 未満です" if min(w, h) < SMALL_SIDE else ""
    return f"→ **{w}×{h}**（canvas {cw}×{ch} ×{scale:g}、16の倍数に丸めて引き伸ばし）{warn}"


def gen_summary(gen: dict) -> str:
    """生成時に使った値の一覧（継承分も含む）。"""
    labels = {"moss": "MOSS段", "anima": "Anima段"}
    return " ／ ".join(f"{labels[s]}：" + ", ".join(f"{p} {v}" for p, v in params.items())
                       for s, params in gen.items() if params)
