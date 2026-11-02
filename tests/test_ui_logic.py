"""Gradio を起動せずに、画面の状態遷移（moa.ui.logic）を確認する（10章）。"""
import json

import pytest

from conftest import EXAMPLES, ROOT, SAMPLES, edit, errors, write
from moa.comfy import effective_gen, load_config
from moa.core import Repository
from moa.core.repository import load_yaml
from moa.ui import logic as L


def _cfg():
    cfg, issues = load_config(ROOT / "config.yaml")
    assert cfg is not None, issues
    return cfg


def pick(repo, sc, nav, target, *choices, slot=None):
    """共有ブラウザで target を選び、choices（"dir:…" / "file:…"）を順にたどる。"""
    nav = L.set_target(nav, target, slot)
    msg = ""
    for c in choices:
        sc, nav, msg = L.browse(repo, sc, nav, c)
    return sc, nav, msg


def open_scene(repo, scene_id="beach_chat__trio"):
    return pick(repo, L.new_sc(), L.new_nav(), "scene", f"file:scenes/{scene_id}.yaml")


def new_draft(repo, action="beach_chat"):
    return pick(repo, L.new_sc(), L.new_nav(), "action", f"file:actions/{action}.yaml")


def fill_cast(repo, sc, nav, cast: dict):
    for slot, cid in cast.items():
        sc, nav, _ = pick(repo, sc, nav, "char", f"file:characters/{cid}.yaml", slot=slot)
    return sc, nav


# ---- 共有ブラウザ ----
def test_initial_listing_for_each_target(repo):
    nav = L.new_nav()
    assert nav["target"] == "scene"
    for target, root, first in (("scene", "scenes", "🎬 海辺で談笑（ポケモントリオ）（beach_chat__trio.yaml）"),
                                ("action", "actions", "📄 海辺で談笑（beach_chat.yaml）"),
                                ("background", "backgrounds", "📄 海辺（beach.yaml）"),
                                ("char", "characters", "📄 カミツレ（BW版）（elesa_bw.yaml）"),
                                ("outfit", "outfits", "📄 カミツレの私服（BW）（elesa_bw_summer.yaml）")):
        choices, crumbs = L.listing(repo, L.set_target(nav, target))
        assert crumbs == root and choices[0][0] == first, (target, choices[:2])


def test_folder_navigation_and_up(repo, data_dir):
    write(data_dir / "scenes" / "pokemon" / "_folder.yaml", "name: ポケモン\n")
    write(data_dir / "scenes" / "pokemon" / "x.yaml",
          "id: x\nbackground: beach\naction: beach_solo\ncast:\n  solo: {character: elesa_bw}\n")
    repo.reload()
    nav = L.set_target(L.new_nav(), "scene")
    choices, _ = L.listing(repo, nav)
    assert ("📁 ポケモン", "dir:scenes/pokemon") in choices

    sc, nav, _ = L.browse(repo, L.new_sc(), nav, "dir:scenes/pokemon")
    choices, crumbs = L.listing(repo, nav)
    assert choices[0] == ("⬆ 上へ", "up") and crumbs == "scenes / ポケモン"
    sc, nav, _ = L.browse(repo, sc, nav, "up")
    assert L.listing(repo, nav)[1] == "scenes"


def test_each_section_remembers_its_own_folder(repo, data_dir):
    write(data_dir / "characters" / "sub" / "c.yaml", "id: c\nname: C\nmoss: m\nanima: a\n")
    repo.reload()
    sc, nav, _ = pick(repo, L.new_sc(), L.new_nav(), "char", "dir:characters/sub", slot="x")
    assert nav["cwd"]["characters"] == "characters/sub" and nav["cwd"]["scenes"] == "scenes"
    assert L.listing(repo, L.set_target(nav, "scene"))[1] == "scenes"


def test_underscore_folders_are_hidden_in_the_browser(repo, data_dir):
    write(data_dir / "actions" / "_draft" / "x.yaml", "id: x\n")
    assert all(not v.startswith("dir:") for _, v in L.listing(repo, L.set_target(L.new_nav(), "action"))[0])


def test_wrong_kind_is_rejected(repo):
    sc, nav = L.new_sc(), L.set_target(L.new_nav(), "action")
    sc2, _, msg = L.apply_entry(repo, sc, nav, repo.entry("background", "beach"))
    assert sc2["action_id"] is None and msg.startswith("⚠")
    sc3, _, msg = L.apply_entry(repo, sc, L.set_target(L.new_nav(), "char", "left"), repo.entry("outfit", "hex_summer"))
    assert sc3["cast"] == {} and msg.startswith("⚠")


# ---- Scene を開く／Action を選ぶ／Background を選ぶ ----
def test_opening_a_scene_loads_everything_and_moves_on_to_the_first_slot(repo):
    sc, nav, msg = open_scene(repo)
    assert (sc["scene_id"], sc["background_id"], sc["bg_source"], sc["action_id"]) == ("beach_chat__trio", "beach", "scene", "beach_chat")
    assert sc["cast"] == {"left": {"character": "skyla_bw", "outfit": None}, "center": {"character": "elesa_bw", "outfit": "none"},
                          "right": {"character": "hex_maniac_xy", "outfit": None}}
    assert sc["gen"] == {"moss": {"steps": 50, "cfg": 3.0}, "anima": {"steps": 14, "cfg": 1.8, "denoise": 0.45}}
    assert sc["open_seq"] == 1 and (nav["target"], nav["slot"]) == ("char", "left") and "Scene を開きました" in msg

    r = L.compile_view(repo, sc)
    assert r.ok and r.moss_prompt == (EXAMPLES / "beach_chat__trio_moss.json").read_text(encoding="utf-8").strip()


def test_selecting_an_action_makes_its_recommended_background_the_initial_choice(repo):
    sc, nav, msg = new_draft(repo)
    assert sc["scene_id"] is None and sc["action_id"] == "beach_chat"
    assert (sc["background_id"], sc["bg_source"]) == ("beach", "action") and "推奨背景 `beach`" in msg
    assert (nav["target"], nav["slot"]) == ("char", "left")
    assert "Action の推奨背景" in L.selection_text(repo, sc)


def test_an_action_without_a_recommended_background_leaves_it_unselected(repo, data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "background: beach\n", "")
    repo2 = Repository(data_dir)
    sc, _, msg = new_draft(repo2)
    assert sc["background_id"] is None and sc["bg_source"] is None and "Background を選んでください" in msg
    r = L.compile_view(repo2, sc)
    assert not r.ok and "Background が未選択" in r.issues[0].message       # コンパイルできず、理由を表示する
    assert "未選択" in L.selection_text(repo2, sc)


def test_a_missing_recommended_background_is_treated_as_none(repo, data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "background: beach", "background: nope")
    sc, _, _ = new_draft(Repository(data_dir))
    assert sc["background_id"] is None


def test_the_users_background_choice_always_wins_over_the_recommendation(repo):
    sc, nav, _ = new_draft(repo)
    sc, nav, msg = pick(repo, sc, nav, "background", "file:backgrounds/white_background.yaml")
    assert (sc["background_id"], sc["bg_source"]) == ("white_background", "user") and "選んだ背景" in L.selection_text(repo, sc)

    sc, nav, _ = pick(repo, sc, nav, "action", "file:actions/beach_solo.yaml")        # 別の Action に選び直しても保つ
    assert (sc["action_id"], sc["background_id"], sc["bg_source"]) == ("beach_solo", "white_background", "user")

    # 手で選ぶ前なら、Action を選び直すと推奨背景が付く
    sc2, nav2, _ = new_draft(repo, "guilty_lineup")
    assert (sc2["background_id"], sc2["bg_source"]) == ("white_background", "action")
    sc2, _, _ = pick(repo, sc2, nav2, "action", "file:actions/beach_solo.yaml")
    assert (sc2["background_id"], sc2["bg_source"]) == ("beach", "action")


def test_the_background_can_be_changed_at_any_time_and_changes_the_output(repo):
    sc, nav, _ = open_scene(repo)
    before = L.compile_view(repo, sc)
    sc, nav, _ = pick(repo, sc, nav, "background", "file:backgrounds/white_background.yaml")
    after = L.compile_view(repo, sc)
    assert sc["bg_source"] == "user" and after.ok
    assert "white background, simple background" in after.anima_prompt and "beach, ocean" in before.anima_prompt
    assert json.loads(after.moss_prompt)["summary"]["caption"].startswith("A plain white background")


def test_reselecting_an_action_keeps_the_casts_of_slots_with_the_same_name(repo):
    sc, nav, _ = open_scene(repo)
    sc["gen"] = {}                                                        # 開いた Scene の gen は引き継がない
    sc2, nav2, _ = pick(repo, sc, nav, "action", "file:actions/guilty_lineup.yaml")
    assert sc2["scene_id"] is None and sc2["scene_name"] == ""
    assert sc2["cast"] == sc["cast"] and sc2["open_seq"] == sc["open_seq"]    # left/center/right が同名 → 引き継ぐ

    sc3, _, _ = pick(repo, sc2, nav2, "action", "file:actions/beach_wave_boy.yaml")   # slots: [girl]
    assert sc3["cast"] == {}


# ---- キャスト枠 ----
def test_pick_a_character_and_an_outfit_per_slot(repo):
    sc, nav, _ = new_draft(repo)
    sc, nav = fill_cast(repo, sc, nav, {"left": "skyla_bw", "center": "elesa_bw"})
    assert sc["cast"]["center"] == {"character": "elesa_bw", "outfit": None}
    assert (nav["target"], nav["slot"]) == ("char", "center")                            # 選択先は残る

    r = L.compile_view(repo, sc)                                           # 右が未選択 → エラー
    assert not r.ok and any("スロット 'right' にキャラクターが割り当てられていません" in i.message for i in r.issues)

    sc, nav, _ = pick(repo, sc, nav, "outfit", "file:outfits/hex_summer.yaml", slot="center")
    assert sc["cast"]["center"] == {"character": "elesa_bw", "outfit": "hex_summer"}
    choices, value = L.outfit_choices(repo, sc, "center")
    assert value == "hex_summer" and choices[-1][1] == "hex_summer"


def test_inherit_label_names_the_layer_the_outfit_comes_from(repo, data_dir):
    sc, nav, _ = new_draft(repo)
    sc, nav = fill_cast(repo, sc, nav, {"left": "skyla_bw", "center": "elesa_bw"})
    assert L.inherit_label(repo, sc, "center") == "未指定（アクション指定：elesa_bw_summer）"
    assert L.inherit_label(repo, sc, "left") == "未指定（キャラ既定：skyla_pilot）"
    assert L.inherit_label(repo, sc, "right") == "未指定（継承）"            # キャラ未選択

    edit(data_dir / "actions" / "beach_chat.yaml", "center: elesa_bw_summer", "center: none")
    edit(data_dir / "characters" / "skyla_bw.yaml", "defaultOutfit: skyla_pilot", "defaultOutfit: none")
    repo2 = Repository(data_dir)
    assert L.inherit_label(repo2, sc, "center") == "未指定（アクション指定：none）"
    assert L.inherit_label(repo2, sc, "left") == "未指定（キャラ既定：none）"
    edit(data_dir / "characters" / "skyla_bw.yaml", "defaultOutfit: none\n", "")
    assert L.inherit_label(Repository(data_dir), sc, "left") == "未指定（服装なし）"


def test_set_outfit_three_valued(repo):
    sc, nav, _ = open_scene(repo)
    assert L.outfit_choices(repo, sc, "center")[1] == "none"
    sc = L.set_outfit(sc, "center", L.INHERIT)
    assert sc["cast"]["center"]["outfit"] is None and L.outfit_choices(repo, sc, "center")[1] == L.INHERIT
    sc = L.set_outfit(sc, "left", "none")
    assert sc["cast"]["left"]["outfit"] == "none"
    sc = L.set_outfit(sc, "bystander", "none")                              # キャラ未選択のスロットでも壊れない
    assert L.build_scene(sc).cast.keys() == {"left", "center", "right"}      # キャラがないスロットは Scene に入れない


def test_character_text_shows_name_path_and_sex(repo, data_dir):
    sc, nav, _ = open_scene(repo)
    assert L.character_text(repo, sc, "left") == "**フウロ（BW版）**  \n`characters/skyla_bw.yaml`"
    assert L.character_text(repo, sc, "nobody") == "_（未選択）_"
    write(data_dir / "characters" / "boy.yaml", "id: boy\nname: 男の子\nsex: male\nmoss: m\nanima: a\n")
    sc["cast"]["left"]["character"] = "boy"
    assert "（male）" in L.character_text(Repository(data_dir), sc, "left")


def test_the_result_summary_shows_rating_people_tags_and_canvas(repo):
    sc, nav, _ = pick(repo, L.new_sc(), L.new_nav(), "scene", "file:scenes/beach_wave_boy__elesa.yaml")
    assert L.summary_text(L.compile_view(repo, sc)) == "**実効 rating：`safe`** ／ **人数タグ：`1girl, 1boy`** ／ canvas：1536×1024"
    sc, nav, _ = pick(repo, L.new_sc(), L.new_nav(), "scene", "file:scenes/beach_solo__elesa.yaml")
    assert "`sensitive`" in L.summary_text(L.compile_view(repo, sc)) and "`1girl, solo`" in L.summary_text(L.compile_view(repo, sc))
    assert L.summary_text(None) == ""


def test_warnings_such_as_bbox_and_people_tags_are_listed(repo, data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "  {{left}}, {{center}}", "  3girls, {{left}}, {{center}}")
    edit(data_dir / "backgrounds" / "beach.yaml", "[-512, -768, -80, 768]", "[-512, -800, -80, 768]")
    sc, _, _ = open_scene(Repository(data_dir))
    text = L.issues_text(L.compile_view(Repository(data_dir), sc).issues)
    assert "⚠️" in text and "人数タグ/solo '3girls'" in text and "左 32px" in text
    assert L.issues_text([]) == "✅ 問題なし"


# ---- ファイル選択（アップロード）は id から索引で元ファイルを特定する ----
@pytest.mark.parametrize("target,rel,expect", [
    ("scene", "scenes/beach_chat__trio.yaml", lambda sc: sc["scene_id"] == "beach_chat__trio"),
    ("action", "actions/beach_solo.yaml", lambda sc: sc["action_id"] == "beach_solo"),
    ("background", "backgrounds/white_background.yaml", lambda sc: sc["background_id"] == "white_background"),
])
def test_upload_resolves_by_id(repo, tmp_path, target, rel, expect):
    up = write(tmp_path / "somewhere_else" / "renamed.yaml", (SAMPLES / "data" / rel).read_text(encoding="utf-8"))
    sc, nav, msg = L.from_upload(repo, L.new_sc(), L.set_target(L.new_nav(), target), str(up))
    assert expect(sc) and "⚠" not in msg
    assert nav["cwd"][L.SECTION[target]] == rel.rsplit("/", 1)[0]


def test_upload_of_a_character_and_an_outfit(repo, tmp_path):
    sc, nav, _ = new_draft(repo)
    up = write(tmp_path / "c.yaml", (SAMPLES / "data" / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8"))
    sc, nav, _ = L.from_upload(repo, sc, L.set_target(nav, "char", "center"), str(up))
    assert sc["cast"]["center"]["character"] == "elesa_bw"
    up = write(tmp_path / "o.yaml", (SAMPLES / "data" / "outfits" / "hex_summer.yaml").read_text(encoding="utf-8"))
    sc, nav, _ = L.from_upload(repo, sc, L.set_target(nav, "outfit", "center"), str(up))
    assert sc["cast"]["center"]["outfit"] == "hex_summer"


def test_upload_uses_the_data_side_when_the_content_differs(repo, tmp_path):
    up = write(tmp_path / "x.yaml", (SAMPLES / "data" / "actions" / "beach_solo.yaml").read_text(encoding="utf-8")
               .replace("name: 海辺で日光浴（ソロ）", "name: 書き換え"))
    sc, _, msg = L.from_upload(repo, L.new_sc(), L.set_target(L.new_nav(), "action"), str(up))
    assert sc["action_id"] == "beach_solo" and "data/ 側" in msg


def test_upload_unknown_id_wrong_kind_and_bad_yaml(repo, tmp_path):
    nav = L.set_target(L.new_nav(), "action")
    unknown = write(tmp_path / "u.yaml", "id: nobody\nname: n\nslots: [a]\nmossCaption: ''\nmossItems: []\nanima: ''\n")
    assert "索引にありません" in L.from_upload(repo, L.new_sc(), nav, str(unknown))[2]
    character = write(tmp_path / "c.yaml", (SAMPLES / "data" / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8"))
    assert "索引にありません" in L.from_upload(repo, L.new_sc(), nav, str(character))[2]     # actions の索引に elesa_bw はない
    assert L.from_upload(repo, L.new_sc(), nav, str(write(tmp_path / "b.yaml", "a: [unclosed\n")))[2].startswith("⚠")
    assert "id がありません" in L.from_upload(repo, L.new_sc(), nav, str(write(tmp_path / "n.yaml", "a: 1\n")))[2]


# ---- 保存（10.2章。保存先の選び方・初期値は tests/test_saveblock.py）----
def new_target(repo, scene_id, folder=None):
    return L.SaveTarget(scene_id, folder=folder or repo.root_dir("scene"))


def over_target(repo, scene_id="beach_chat__trio"):
    return L.SaveTarget(scene_id, path=repo.path_of("scene", scene_id))


def save_new(repo, sc, scene_id, name="", generated=None, folder=None, **kw):
    """新しいファイルとして保存する（folder 省略時は scenes/ 直下）。"""
    return L.save(repo, sc, new_target(repo, scene_id, folder), name, generated, **kw)


def test_new_scene_is_saved_in_the_chosen_folder_and_the_full_path_is_shown(repo, data_dir):
    sc, nav, _ = new_draft(repo, "beach_solo")
    sc, nav = fill_cast(repo, sc, nav, {"solo": "skyla_bw"})
    sc2, msg = save_new(repo, sc, "beach_solo__skyla", "スカイラ", folder=data_dir / "scenes" / "pokemon")
    path = data_dir.resolve() / "scenes" / "pokemon" / "beach_solo__skyla.yaml"
    assert path.exists() and f"保存しました：`{path}`" in msg                              # フルパスを表示する
    assert sc2["scene_id"] == "beach_solo__skyla" and sc2["scene_name"] == "スカイラ"
    data = load_yaml(path)
    assert (data["background"], data["action"], data["name"]) == ("beach", "beach_solo", "スカイラ")
    assert data["compiled"]["rating"] == "sensitive"
    assert errors(Repository(data_dir).issues) == []


@pytest.mark.parametrize("scene_id", ["Bad Name", "日本語", "a-b"])
def test_an_invalid_id_is_rejected(repo, scene_id):
    sc, nav, _ = open_scene(repo)
    assert "半角英小文字" in save_new(repo, sc, scene_id)[1]


def test_save_guards(repo):
    assert "Action が未選択" in save_new(repo, L.new_sc(), "x")[1]
    sc, nav, _ = new_draft(repo)
    sc, nav = fill_cast(repo, sc, nav, {"left": "skyla_bw"})                 # 右が未選択
    assert "検証エラー" in save_new(repo, sc, "ok")[1]
    sc["background_id"] = None
    assert "Background が未選択" in save_new(repo, sc, "ok")[1]


# ---- 生成結果の記録（13.4章）----
def snapshot_for(repo, sc, gp=None, partial=False, **override):
    r = L.compile_view(repo, sc)
    snap = {"seed": 4242, "images": ["output/m.png"] + ([] if partial else ["output/a.png"]), "partial": partial,
            "failed_stage": "anima" if partial else None, "error": None,
            "moss_prompt": r.moss_prompt, "anima_prompt": r.anima_prompt, "negative_applied": r.apply_negative,
            "negative_moss": r.negative_moss, "negative_anima": r.negative_anima,
            "background": r.background_id, "action": r.action_id, "canvas": dict(r.canvas),
            "gen": effective_gen(_cfg(), (gp or L.new_gp())["gen"]), "moss_ref": None}
    snap.update(override)
    return snap


def saved(data_dir, scene_id):
    return load_yaml(data_dir / "scenes" / f"{scene_id}.yaml")


def test_a_matching_generation_is_recorded_with_seed_images_and_the_used_gen(repo, data_dir):
    sc, nav, _ = open_scene(repo)
    gp = L.sync_gp(sc, L.new_gp())
    eff = effective_gen(_cfg(), gp["gen"])
    _, msg = save_new(repo, sc, "rec", "", snapshot_for(repo, sc, gp), gen_params=gp["gen"], effective=eff)
    c = saved(data_dir, "rec")["compiled"]
    assert "🖼" in msg and "4242" in msg
    assert (c["seed"], c["images"], c["gen"]) == (4242, ["output/m.png", "output/a.png"], eff) and "partial" not in c


def test_a_partial_result_is_recorded_as_partial(repo, data_dir):
    sc, nav, _ = open_scene(repo)
    gp = L.sync_gp(sc, L.new_gp())
    _, msg = save_new(repo, sc, "part", "", snapshot_for(repo, sc, gp, partial=True), gen_params=gp["gen"],
                    effective=effective_gen(_cfg(), gp["gen"]))
    c = saved(data_dir, "part")["compiled"]
    assert c["partial"] is True and c["images"] == ["output/m.png"] and "部分結果" in msg


def test_a_failed_moss_stage_records_nothing(repo, data_dir):
    sc, nav, _ = open_scene(repo)
    snap = snapshot_for(repo, sc, images=[], failed_stage="moss", partial=False)
    save_new(repo, sc, "none", "", snap, gen_params={}, effective=effective_gen(_cfg(), {}))
    c = saved(data_dir, "none")["compiled"]
    assert c["seed"] is None and c["images"] == []


@pytest.mark.parametrize("change,named", [
    ("cast", ["mossPrompt", "animaPrompt"]),
    ("negative", ["ネガティブ適用"]),
    ("background", ["Background", "canvas"]),
    ("gen", ["gen.anima.steps"]),
])
def test_a_changed_item_blocks_recording_and_is_named(repo, data_dir, change, named):
    write(data_dir / "backgrounds" / "wide.yaml", "id: wide\nname: w\ncanvas: { w: 1920, h: 1080 }\nmossCaption: ''\nanima: ''\n")
    repo = Repository(data_dir)
    sc, nav, _ = open_scene(repo)
    gp = L.sync_gp(sc, L.new_gp())
    snap = snapshot_for(repo, sc, gp, partial=(change == "cast"))
    if change == "cast":
        sc["cast"]["center"]["character"] = "skyla_bw"
    elif change == "negative":
        sc["apply_negative"] = True
    elif change == "background":
        sc["background_id"] = "wide"
    else:
        gp = L.set_override(gp, "anima", "steps", 20)                 # 生成後にスライダーを動かした
    _, msg = save_new(repo, sc, "blocked", "", snap, gen_params=gp["gen"], effective=effective_gen(_cfg(), gp["gen"]))
    c = saved(data_dir, "blocked")["compiled"]
    assert c["seed"] is None and c["images"] == [] and "gen" not in c
    assert "記録していません" in msg and all(n in msg for n in named), msg
    if change == "cast":
        assert "部分結果も破棄" in msg
    if change == "gen":
        assert saved(data_dir, "blocked")["gen"]["anima"]["steps"] == 20           # Scene.gen には今の設定が入る


# ---- 生成パラメータ（Scene.gen）----
def test_opening_a_scene_loads_its_gen_into_gp_and_reopening_discards_unsaved_changes(repo):
    sc, nav, _ = open_scene(repo)
    gp = L.sync_gp(sc, L.new_gp())
    assert gp["gen"] == sc["gen"] and gp["seq"] == 1
    gp = L.set_override(gp, "moss", "steps", 20)

    nav2 = L.set_target(nav, "action")
    sc2, _, _ = L.browse(repo, sc, nav2, "file:actions/beach_solo.yaml")
    assert L.sync_gp(sc2, gp) == gp                                        # Action を選び直しても操作中の値は残る
    sc3, _, _ = pick(repo, sc2, nav, "background", "file:backgrounds/white_background.yaml")
    assert L.sync_gp(sc3, gp) == gp                                        # Background でも同じ

    sc4, _, _ = pick(repo, sc3, nav, "scene", "file:scenes/beach_chat__trio.yaml")
    assert L.sync_gp(sc4, gp)["gen"] == sc["gen"]                          # Scene を開き直すと、Scene.gen に戻る


def test_override_clear_and_param_state(repo):
    cfg = _cfg()
    gp = L.new_gp()
    st = L.param_state(cfg, gp, "moss", "steps")
    assert (st["value"], st["source"], st["overridden"], st["enabled"]) == (50, "workflow", False, True)
    gp = L.set_override(gp, "anima", "denoise", 0.30000000000000004)
    gp = L.set_override(gp, "anima", "steps", 14.0)
    assert gp["gen"] == {"anima": {"steps": 14, "denoise": 0.3}} and isinstance(gp["gen"]["anima"]["steps"], int)
    st = L.param_state(cfg, gp, "anima", "steps")
    assert (st["value"], st["source"], st["overridden"]) == (14, "scene", True) and "Scene" in L.source_text(st)
    assert "ワークフロー" in L.source_text(L.param_state(cfg, gp, "anima", "cfg"))
    gp = L.clear_override(gp, "anima", "steps")
    assert gp["gen"] == {"anima": {"denoise": 0.3}} and L.clear_override(gp, "anima", "denoise")["gen"] == {}
    off = L.param_state(None, gp, "moss", "steps")
    assert off["enabled"] is False and off["value"] is None
    assert L.gen_summary({"moss": {"steps": 50}, "anima": {"denoise": 0.45}}) == "MOSS段：steps 50 ／ Anima段：denoise 0.45"


def test_only_the_overridden_items_are_saved_as_scene_gen(repo, data_dir):
    sc, nav, _ = new_draft(repo, "beach_solo")
    sc, nav = fill_cast(repo, sc, nav, {"solo": "elesa_bw"})
    save_new(repo, sc, "g1", "", None, gen_params={"moss": {"steps": 20}, "anima": {"denoise": 0.45}})
    assert saved(data_dir, "g1")["gen"] == {"moss": {"steps": 20}, "anima": {"denoise": 0.45}}
    save_new(repo, sc, "g2", "", None, gen_params={})
    assert "gen" not in saved(data_dir, "g2")


# ---- エディタ導入に伴う変更（EDITOR_SPEC 12章 / 13章-14）----
def _bump(path):
    import os
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_opening_a_scene_notes_that_master_data_changed(repo, data_dir):
    sc, nav, msg = open_scene(repo)
    assert "保存時から内容が変わっています" not in msg                 # compiled が無い Scene では表示しない
    sc, msg = L.save(repo, sc, over_target(repo), "", None)
    assert "保存しました" in msg
    repo.reload()
    assert "保存時から内容が変わっています" not in open_scene(repo)[2]

    edit(data_dir / "actions" / "beach_chat.yaml", "Three adult women", "Three young adult women")   # 行為を編集
    repo.reload()
    note = open_scene(repo)[2]
    assert "保存時から内容が変わっています" in note and "MOSS" in note and "Anima" not in note

    edit(data_dir / "characters" / "skyla_bw.yaml", "red hair", "blue hair")                         # Anima 側も
    repo.reload()
    note = open_scene(repo)[2]
    assert "MOSS" in note and "Anima" in note


def test_a_new_scene_does_not_get_gen_written(repo, data_dir):
    sc, nav, _ = new_draft(repo, "beach_solo")
    sc, nav = fill_cast(repo, sc, nav, {"solo": "skyla_bw"})
    save_new(repo, sc, "beach_solo__nogen", "", None)
    assert "gen" not in load_yaml(data_dir / "scenes" / "beach_solo__nogen.yaml")        # 動かした項目だけ書く


def test_scene_save_detects_external_change_and_offers_two_choices(repo, data_dir):
    path = data_dir / "scenes" / "beach_chat__trio.yaml"
    sc, nav, _ = open_scene(repo)
    path.write_text(path.read_text(encoding="utf-8") + "# 外部で追記\n", encoding="utf-8")
    _bump(path)

    sc2, msg = L.save(repo, sc, over_target(repo), "外部変更の後", None)
    assert msg.startswith(L.EXTERNAL_PREFIX)
    assert "外部変更の後" not in path.read_text(encoding="utf-8")                          # 止まった

    sc3, msg = L.save(repo, sc, over_target(repo), "外部変更の後", None, force=True)   # [上書き保存]
    assert "保存しました" in msg and load_yaml(path)["name"] == "外部変更の後"

    path.write_text(path.read_text(encoding="utf-8") + "# もう一度\n", encoding="utf-8")
    _bump(path)
    assert L.save(repo, sc3, over_target(repo), "", None)[1].startswith(L.EXTERNAL_PREFIX)
    sc4, nav4, msg = L.reopen_scene(repo, sc3, nav)                                       # [再読み込み（編集を破棄）]
    assert "開きました" in msg and "保存しました" in L.save(repo, sc4, over_target(repo), "", None)[1]
