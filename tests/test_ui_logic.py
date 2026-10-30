"""Gradio を起動せずに、画面の状態遷移（moa.ui.logic）を確認する。"""
from conftest import SAMPLES, edit, write
from moa.core import Repository
from moa.ui import logic as L


def _open_situation(repo):
    sc, nav = L.new_sc(), L.new_nav()
    sc, nav, _ = L.browse(repo, sc, nav, "dir:situations/beach")
    return L.browse(repo, sc, nav, "file:situations/beach/beach_chat.yaml")


def test_initial_listing_and_folder_navigation(repo):
    nav = L.new_nav()
    choices, crumbs = L.listing(repo, nav)
    assert choices == [("📁 海辺", "dir:situations/beach")] and crumbs == "situations"

    sc, nav, _ = L.browse(repo, L.new_sc(), nav, "dir:situations/beach")
    choices, crumbs = L.listing(repo, nav)
    assert choices[0] == ("⬆ 上へ", "up") and crumbs == "situations / 海辺"
    assert any(v == "file:situations/beach/beach_chat.yaml" for _, v in choices)

    sc, nav, _ = L.browse(repo, sc, nav, "up")
    assert L.listing(repo, nav)[1] == "situations"


def test_choosing_situation_advances_to_first_slot(repo):
    sc, nav, msg = _open_situation(repo)
    assert sc["situation_id"] == "beach_chat" and sc["scene_id"] is None and sc["cast"] == {}
    assert (nav["target"], nav["slot"]) == ("char", "left")
    assert L.slots_of(repo, sc) == ["left", "center", "right"]


def test_opening_scene_loads_cast(repo):
    sc, nav, _ = L.browse(repo, L.new_sc(), L.new_nav(), "dir:situations/beach")
    sc, nav, _ = L.browse(repo, sc, nav, "file:situations/beach/beach_chat__trio.yaml")
    assert sc["scene_id"] == "beach_chat__trio"
    assert sc["cast"] == {"left": "skyla_bw", "center": "elesa_bw", "right": "hex_maniac_xy"}
    r = L.compile_view(repo, sc)
    assert r.ok and r.apply_negative is False


def test_pick_character_and_outfit_per_slot(repo):
    sc, nav, _ = _open_situation(repo)
    # 共有ブラウザは開いたまま、選択先だけが変わる
    nav = L.set_target(nav, "char", "center")
    assert L.listing(repo, nav)[1] == "characters"
    sc, nav, _ = L.browse(repo, sc, nav, "dir:characters/pokemon")
    sc, nav, msg = L.browse(repo, sc, nav, "file:characters/pokemon/elesa_bw.yaml")
    assert sc["cast"] == {"center": "elesa_bw"} and "center" in msg
    assert (nav["target"], nav["slot"]) == ("char", "center")  # 選択先は残る

    nav = L.set_target(nav, "outfit", "center")
    sc, nav, _ = L.browse(repo, sc, nav, "file:outfits/pokemon/hex_summer.yaml")
    assert sc["outfits"] == {"center": "hex_summer"}
    choices, value = L.outfit_choices(repo, sc, "center")
    assert value == "hex_summer" and choices[-1][1] == "hex_summer"


def test_wrong_kind_is_rejected(repo):
    sc, nav, _ = _open_situation(repo)
    nav = L.set_target(nav, "char", "left")
    sc2, _, msg = L.apply_entry(repo, sc, nav, repo.entry("outfit", "hex_summer"))
    assert sc2["cast"] == {} and msg.startswith("⚠")


def test_inherit_label_shows_where_the_outfit_comes_from(repo, data_dir):
    sc, nav, _ = _open_situation(repo)
    sc["cast"] = {"center": "elesa_bw"}
    assert L.inherit_label(repo, sc, "center") == "未指定（キャラのdefaultOutfit：elesa_bw_summer）"

    edit(data_dir / "situations" / "beach" / "beach_chat.yaml",
         "mossPrompt: |", "outfits:\n  center: hex_summer\n  left: none\nmossPrompt: |")
    repo.reload()
    assert L.inherit_label(repo, sc, "center") == "未指定（シチュエーション指定：hex_summer）"
    sc["cast"]["left"] = "skyla_bw"
    assert L.inherit_label(repo, sc, "left") == "未指定（シチュエーション指定：none）"
    sc["cast"]["right"] = "hex_maniac_xy"
    assert L.inherit_label(repo, sc, "right") == "未指定（キャラのdefaultOutfit：hex_summer）"

    sc = L.set_outfit(sc, "center", "none")
    assert L.outfit_choices(repo, sc, "center")[1] == "none"
    sc = L.set_outfit(sc, "center", L.INHERIT)
    assert sc["outfits"]["center"] is None


# ---- ファイル選択（アップロード）は id から索引で元ファイルを特定する ----
def test_upload_resolves_by_id_and_uses_data_side(repo, tmp_path):
    sc, nav, _ = _open_situation(repo)
    nav = L.set_target(nav, "char", "right")
    up = tmp_path / "somewhere_else" / "renamed_copy.yaml"  # 元パスは取れない前提
    write(up, (SAMPLES / "data/characters/pokemon/skyla_bw.yaml").read_text(encoding="utf-8"))

    sc2, nav2, msg = L.from_upload(repo, sc, nav, str(up))
    assert sc2["cast"]["right"] == "skyla_bw" and "⚠" not in msg
    assert nav2["cwd"]["characters"] == "characters/pokemon"

    write(up, up.read_text(encoding="utf-8").replace("name: フウロ（BW版）", "name: 書き換え"))
    sc3, _, msg = L.from_upload(repo, sc, nav, str(up))
    assert sc3["cast"]["right"] == "skyla_bw" and "data/ 側" in msg


def test_upload_unknown_id_and_bad_yaml(repo, tmp_path):
    sc, nav, _ = _open_situation(repo)
    nav = L.set_target(nav, "char", "left")
    up = write(tmp_path / "x.yaml", "id: nobody\nname: n\nmoss: m\nanima: a\n")
    sc2, _, msg = L.from_upload(repo, sc, nav, str(up))
    assert sc2["cast"] == {} and "索引にありません" in msg
    bad = write(tmp_path / "y.yaml", "a: [unclosed\n")
    assert L.from_upload(repo, sc, nav, str(bad))[2].startswith("⚠")


def test_upload_scene_file_opens_it(repo, tmp_path):
    nav = L.new_nav()
    up = write(tmp_path / "t.yaml", (SAMPLES / "data/situations/beach/beach_chat__trio.yaml").read_text(encoding="utf-8"))
    sc, nav, _ = L.from_upload(repo, L.new_sc(), nav, str(up))
    assert sc["scene_id"] == "beach_chat__trio" and sc["cast"]["center"] == "elesa_bw"


# ---- 保存 ----
def _open_trio(repo):
    sc, nav, _ = L.browse(repo, L.new_sc(), L.new_nav(), "dir:situations/beach")
    return L.browse(repo, sc, nav, "file:situations/beach/beach_chat__trio.yaml")[0]


def test_save_with_name_creates_new_scene(repo, data_dir):
    sc = _open_trio(repo)
    sc2, msg = L.save(repo, sc, "swap", "差し替え版")
    assert "新規保存" in msg and sc2["scene_id"] == "beach_chat__swap"
    assert (data_dir / "situations" / "beach" / "beach_chat__swap.yaml").exists()
    assert repo.scene("beach_chat__swap").name == "差し替え版"   # 保存後に再読み込み済み


def test_empty_name_auto_numbers_and_never_overwrites(repo, data_dir):
    trio_file = data_dir / "situations" / "beach" / "beach_chat__trio.yaml"
    before = trio_file.read_text(encoding="utf-8")
    sc = _open_trio(repo)

    sc1, msg = L.save(repo, sc, "", "")            # 開いているのは trio → trio_1
    assert sc1["scene_id"] == "beach_chat__trio_1" and "新規保存" in msg
    sc2, _ = L.save(repo, sc1, "  ", "")           # trio_1 から → trio_2（_1 を重ねない）
    assert sc2["scene_id"] == "beach_chat__trio_2"
    sc3, _ = L.save(repo, sc, "", "")              # trio から再度 → 空き番号の trio_3
    assert sc3["scene_id"] == "beach_chat__trio_3"
    assert trio_file.read_text(encoding="utf-8") == before  # 元のSceneは無傷


def test_empty_name_without_opened_scene_uses_default_base(repo):
    sc, nav, _ = _open_situation(repo)
    sc["cast"] = {"left": "skyla_bw", "center": "elesa_bw", "right": "hex_maniac_xy"}
    sc2, msg = L.save(repo, sc, "", "")
    assert sc2["scene_id"] == "beach_chat__scene_1" and "新規保存" in msg


def test_overwrite_only_when_same_name_is_given_explicitly(repo, data_dir):
    sc = _open_trio(repo)
    sc["cast"]["center"] = "skyla_bw"
    sc2, msg = L.save(repo, sc, "trio", "")   # 明示：trio
    assert "上書き保存" in msg and sc2["scene_id"] == "beach_chat__trio"
    assert Repository(data_dir).scene("beach_chat__trio").cast["center"] == "skyla_bw"


def test_save_guards(repo):
    assert "未選択" in L.save(repo, L.new_sc(), "x", "")[1]
    sc, nav, _ = _open_situation(repo)
    assert "保存できません" in L.save(repo, sc, "ok", "")[1]       # キャスト未割当
    assert "半角" in L.save(repo, {**sc, "cast": {"left": "skyla_bw"}}, "Bad Name", "")[1]


# ---- Situation の選び直し ----
def test_reselect_situation_keeps_matching_slots(repo, data_dir):
    write(data_dir / "situations" / "beach" / "beach_duo.yaml", """\
id: beach_duo
name: 海辺の二人
rating: safe
slots: [left, right, extra]
canvas: { w: 1024, h: 1024 }
mossPrompt: |
  {"summary": {"meta": [], "canvas": "single", "caption": "{{left}} {{left.outfit}} {{right}} {{extra}}"}}
animaPrompt: |
  2girls, {{left}}, {{right}}, {{extra}}
""")
    repo.reload()
    sc = _open_trio(repo)
    sc["outfits"] = {"left": "none", "center": "hex_summer"}

    nav = L.set_target(L.new_nav(), "char", "left")
    nav = L.set_target(nav, "situation")                      # 「選び直す」ボタン
    assert L.listing(repo, nav)[1] == "situations"            # Situation 一覧に戻る
    sc2, nav2, msg = L.browse(repo, sc, nav, "file:situations/beach/beach_duo.yaml")

    assert sc2["situation_id"] == "beach_duo" and sc2["scene_id"] is None
    assert sc2["cast"] == {"left": "skyla_bw", "right": "hex_maniac_xy"}   # center は消え、同名スロットは残る
    assert sc2["outfits"] == {"left": "none"}
    assert (nav2["target"], nav2["slot"]) == ("char", "extra")             # 未選択スロットへ進む
    assert not L.compile_view(repo, sc2).ok                                # extra が未割当


# ---- 生成結果の記録は、今の内容と一致するときだけ ----
def _generated(repo, sc, seed=4242):
    r = L.compile_view(repo, sc)
    return {"seed": seed, "images": ["output/m.png", "output/a.png"], "moss_prompt": r.moss_prompt,
            "anima_prompt": r.anima_prompt, "negative_applied": r.apply_negative}


def test_save_records_generation_when_it_matches(repo, data_dir):
    sc = _open_trio(repo)
    gen = _generated(repo, sc)
    sc2, msg = L.save(repo, sc, "gen", "", gen)
    assert "🖼" in msg and "4242" in msg
    from moa.core.repository import load_yaml
    c = load_yaml(data_dir / "situations" / "beach" / "beach_chat__gen.yaml")["compiled"]
    assert c["seed"] == 4242 and c["images"] == ["output/m.png", "output/a.png"]


def test_save_does_not_record_stale_generation(repo, data_dir):
    sc = _open_trio(repo)
    gen = _generated(repo, sc)
    sc["cast"]["center"] = "skyla_bw"                 # 生成後にキャストを変えた
    _, msg = L.save(repo, sc, "stale", "", gen)
    assert "記録していません" in msg
    from moa.core.repository import load_yaml
    c = load_yaml(data_dir / "situations" / "beach" / "beach_chat__stale.yaml")["compiled"]
    assert c["seed"] is None and c["images"] == []

    sc["apply_negative"] = True                       # ネガティブ適用の切り替えも「別の内容」
    gen2 = _generated(repo, {**sc, "apply_negative": False})
    assert "記録していません" in L.save(repo, sc, "stale2", "", gen2)[1]


# ---- 生成パラメータ（gen）----
def _cfg():
    from conftest import ROOT
    from moa.comfy import load_config
    cfg, issues = load_config(ROOT / "config.yaml")
    assert cfg is not None, issues
    return cfg


def test_opening_scene_loads_its_gen_into_gp(repo, data_dir):
    edit(data_dir / "situations" / "beach" / "beach_chat__trio.yaml", "applyNegative: false",
         "applyNegative: false\ngen:\n  anima: { denoise: 0.45 }")
    repo.reload()
    sc = _open_trio(repo)
    assert sc["gen"] == {"anima": {"denoise": 0.45}}
    assert L.sync_gp(sc, L.new_gp())["gen"] == {"anima": {"denoise": 0.45}}
    assert L.build_scene(sc).gen == {"anima": {"denoise": 0.45}}


def test_gp_is_kept_on_situation_reselect_and_replaced_when_a_scene_is_opened(repo):
    sc = _open_trio(repo)
    gp = L.set_override(L.sync_gp(sc, L.new_gp()), "moss", "steps", 20)

    nav = L.set_target(L.new_nav(), "situation")
    nav["cwd"]["situations"] = "situations/beach"
    sc2, _, _ = L.apply_entry(repo, sc, nav, repo.entry("situation", "beach_chat"))
    assert L.sync_gp(sc2, gp) == gp                                  # Situation を選び直しても操作中の値は残る

    sc3, _, _ = L.apply_entry(repo, sc2, nav, repo.entry("scene", "beach_chat__trio"))
    assert L.sync_gp(sc3, gp)["gen"] == {}                           # Scene を開き直すと、Scene.gen（なし）に戻る


def test_set_and_clear_override_normalize_and_prune():
    gp = L.set_override(L.new_gp(), "anima", "denoise", 0.30000000000000004)
    gp = L.set_override(gp, "anima", "steps", 14.0)
    assert gp["gen"] == {"anima": {"steps": 14, "denoise": 0.3}} and isinstance(gp["gen"]["anima"]["steps"], int)
    gp = L.clear_override(gp, "anima", "steps")
    assert gp["gen"] == {"anima": {"denoise": 0.3}}
    assert L.clear_override(gp, "anima", "denoise")["gen"] == {}     # 空になった段は消える
    assert L.clear_override(gp, "moss", "steps") == gp               # 無い項目の削除は何も起きない


def test_param_state_shows_where_each_value_comes_from():
    cfg = _cfg()
    gp = L.new_gp()
    st = L.param_state(cfg, gp, "moss", "steps")
    assert (st["value"], st["source"], st["overridden"], st["enabled"]) == (50, "workflow", False, True)
    assert "ワークフロー" in L.source_text(st)

    gp = L.set_override(gp, "moss", "steps", 20)
    st = L.param_state(cfg, gp, "moss", "steps")
    assert (st["value"], st["source"], st["overridden"]) == (20, "scene", True) and "Scene" in L.source_text(st)
    assert L.param_state(cfg, gp, "anima", "denoise")["value"] == 0.35   # 他の項目は影響しない

    off = L.param_state(None, gp, "moss", "steps")
    assert off["enabled"] is False and off["value"] is None


def test_save_stores_only_the_overridden_items(repo, data_dir):
    from moa.core.repository import load_yaml
    sc = _open_trio(repo)
    sc2, _ = L.save(repo, sc, "g", "", None, gen_params={"moss": {"steps": 20}, "anima": {"denoise": 0.45}})
    data = load_yaml(data_dir / "situations" / "beach" / "beach_chat__g.yaml")
    assert data["gen"] == {"moss": {"steps": 20}, "anima": {"denoise": 0.45}} and sc2["gen"] == data["gen"]

    L.save(repo, sc, "empty", "", None, gen_params={})
    assert "gen" not in load_yaml(data_dir / "situations" / "beach" / "beach_chat__empty.yaml")


def test_save_without_gen_params_keeps_the_opened_scenes_gen(repo, data_dir):
    from moa.core.repository import load_yaml
    edit(data_dir / "situations" / "beach" / "beach_chat__trio.yaml", "applyNegative: false",
         "applyNegative: false\ngen:\n  moss: { cfg: 3.0 }")
    repo.reload()
    sc = _open_trio(repo)
    L.save(repo, sc, "keep", "", None)
    assert load_yaml(data_dir / "situations" / "beach" / "beach_chat__keep.yaml")["gen"] == {"moss": {"cfg": 3.0}}


def test_generation_records_the_values_it_used_even_if_sliders_moved_after(repo, data_dir):
    from moa.core.repository import load_yaml
    sc = _open_trio(repo)
    used = {"moss": {"steps": 50, "cfg": 5}, "anima": {"steps": 30, "cfg": 2, "denoise": 0.35}}
    generated = {**_generated(repo, sc), "gen": used}
    _, msg = L.save(repo, sc, "later", "", generated, gen_params={"moss": {"steps": 20}})   # 生成後に動かした
    data = load_yaml(data_dir / "situations" / "beach" / "beach_chat__later.yaml")
    assert "🖼" in msg
    assert data["gen"] == {"moss": {"steps": 20}}                    # Scene.gen は今のスライダー
    assert data["compiled"]["gen"] == used                           # compiled.gen は生成時に使った値
    assert data["compiled"]["seed"] == 4242
