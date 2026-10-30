from dataclasses import replace

import pytest

from conftest import edit, errors, warnings, write
from moa.core import Repository, Scene, compile_scene, new_scene_id, resolve_outfit, save_scene
from moa.core.textutil import clean_tags, dedupe_tags, join_negative, natural_key
from moa.core.tree import breadcrumb, list_folder


# ---- textutil ----
def test_clean_tags_keeps_duplicates_drops_empties():
    assert clean_tags(" a, , b ,, a,\n") == "a, b, a"
    assert clean_tags("") == "" and clean_tags(None) == ""


def test_dedupe_is_case_insensitive_first_wins():
    assert dedupe_tags(["Blurry", "text", "blurry", "TEXT", "x"]) == ["Blurry", "text", "x"]
    assert join_negative(["a, B", None, "b, c"]) == "a, B, c"


def test_natural_key_reads_numbers():
    assert sorted(["10_b", "2_a", "1_z"], key=natural_key) == ["1_z", "2_a", "10_b"]


# ---- resolve（未指定 / none / null の区別）----
def test_resolve_three_valued():
    r = lambda s, si, d: resolve_outfit("c", s, si, d)  # noqa: E731
    assert r({}, {}, "def") == resolve_outfit("c", {"c": None}, {}, "def")  # キーなし == null
    assert r({}, {}, "def").source == "character"
    assert r({}, {"c": "none"}, "def").outfit_id is None            # 明示noneは継承しない
    assert r({}, {"c": "none"}, "def").source == "none_situation"
    assert r({"c": "a"}, {"c": "b"}, "def").outfit_id == "a"
    assert r({"c": None}, {"c": "b"}, "def").outfit_id == "b"       # nullは下位に委譲
    assert r({}, {}, None).source == "unset"


# ---- repository ----
def test_scene_vs_situation_detection(repo):
    assert repo.scene("beach_chat__trio") is not None
    assert repo.situation("beach_chat") is not None
    assert repo.situation("beach_chat__trio") is None
    assert not errors(repo.issues)


def test_schema_violation_is_reported_and_skipped(repo, data_dir):
    write(data_dir / "characters" / "bad.yaml", "id: bad\nname: x\nmoss: m\n")  # anima欠落
    repo.reload()
    assert repo.character("bad") is None
    assert any("bad.yaml" in str(i) and "anima" in i.message for i in errors(repo.issues))


def test_id_must_match_filename_and_be_unique(repo, data_dir):
    write(data_dir / "characters" / "x.yaml", "id: y\nname: x\nmoss: m\nanima: a\n")
    write(data_dir / "characters" / "zzz_other" / "skyla_bw.yaml",
          "id: skyla_bw\nname: dup\nmoss: m\nanima: a\n")
    repo.reload()
    msgs = [i.message for i in errors(repo.issues)]
    assert any("一致しません" in m for m in msgs)
    assert any("重複" in m for m in msgs)
    assert repo.character("skyla_bw").name == "フウロ（BW版）"  # 先に見つかった方が残る


def test_dangling_references_are_errors(repo, data_dir):
    edit(data_dir / "characters" / "pokemon" / "elesa_bw.yaml", "defaultOutfit: elesa_bw_summer",
         "defaultOutfit: nope")
    repo.reload()
    assert any("defaultOutfit 'nope'" in i.message for i in errors(repo.issues))


# ---- validate ----
def test_undeclared_slot_in_template_is_error(repo, data_dir, trio):
    edit(data_dir / "situations" / "beach" / "beach_chat.yaml", "{{left}}, {{center}}", "{{left}}, {{ghost}}, {{center}}")
    repo.reload()
    r = compile_scene(repo, trio)
    assert not r.ok and any("ghost" in i.message for i in r.issues)


def test_unknown_placeholder_form_is_error(repo, data_dir, trio):
    edit(data_dir / "situations" / "beach" / "beach_chat.yaml", "{{left}}, {{center}}", "{{left.moss}}, {{center}}")
    repo.reload()
    assert not compile_scene(repo, trio).ok


def test_unassigned_slot_is_error_and_unused_slot_is_warning(repo, data_dir, trio):
    r = compile_scene(repo, replace(trio, cast={"left": "skyla_bw", "center": "elesa_bw"}))
    assert not r.ok and any("right" in i.message for i in errors(r.issues))
    assert r.moss_prompt == ""

    edit(data_dir / "situations" / "beach" / "beach_chat.yaml",
         "slots: [left, center, right]", "slots: [left, center, right, extra]")
    repo.reload()
    assert any("extra" in i.message for i in warnings(repo.issues))


def test_invalid_moss_json_is_error(repo, data_dir, trio):
    edit(data_dir / "situations" / "beach" / "beach_chat.yaml", '"bbox_format": "yxyx"', '"bbox_format": ')
    repo.reload()
    assert any("JSON" in i.message for i in errors(compile_scene(repo, trio).issues))


def test_explicit_none_in_situation_blocks_default_outfit(repo, data_dir, trio):
    edit(data_dir / "situations" / "beach" / "beach_chat.yaml",
         "mossPrompt: |", "outfits:\n  center: none\n  left: null\nmossPrompt: |")
    repo.reload()
    r = compile_scene(repo, trio)
    slots = {s.slot: s for s in r.slots}
    assert slots["center"].outfit_id is None                  # none: 服が付かない
    assert slots["left"].outfit_id == "skyla_pilot"           # null: 未指定 → defaultOutfit


# ---- tree ----
def test_tree_order_labels_and_natural_sort(repo, data_dir):
    base = data_dir / "situations"
    for name in ("10_b", "2_a", "1_z"):
        write(base / name / "_folder.yaml", f"name: 表示{name}\n")
    repo.reload()
    labels = [it.label for it in list_folder(repo, base)]
    assert labels[:3] == ["📁 表示1_z", "📁 表示2_a", "📁 表示10_b"]

    write(base / "_folder.yaml", "order:\n  - 10_b\n  - beach\n")
    repo.reload()
    assert [it.path.name for it in list_folder(repo, base)][:3] == ["10_b", "beach", "1_z"]


def test_folder_files_show_name_and_filename(repo, data_dir):
    items = list_folder(repo, data_dir / "situations" / "beach")
    assert [i.label for i in items] == ["📄 海辺で談笑（beach_chat.yaml）",
                                         "🎬 海辺で談笑（ポケモントリオ）（beach_chat__trio.yaml）"]
    crumbs = breadcrumb(repo, data_dir / "situations", data_dir / "situations" / "beach")
    assert [c[0] for c in crumbs] == ["situations", "海辺"]


# ---- scene_io ----
def test_save_preserves_comments_seed_and_images(repo, data_dir, trio):
    path = repo.path_of("scene", trio.id)
    edit(path, "  images: []", "  images: [a.png]")
    repo.reload()
    scene = repo.scene(trio.id)
    r = compile_scene(repo, replace(scene, apply_negative=True))
    save_scene(repo, replace(scene, apply_negative=True), r)

    text = path.read_text(encoding="utf-8")
    assert "# 実際にコンパイルすると" in text and "# このシーンのコンパイル結果は" in text
    repo2 = Repository(data_dir)
    assert not errors(repo2.issues)
    from moa.core.repository import load_yaml
    data = load_yaml(path)
    assert data["compiled"]["mossPrompt"].strip() == r.moss_prompt
    assert data["compiled"]["animaPrompt"].strip() == r.anima_prompt
    assert data["compiled"]["negativeApplied"] is True and data["applyNegative"] is True
    assert data["compiled"]["images"] == ["a.png"] and data["compiled"]["seed"] is None


def test_save_new_scene_next_to_situation(repo, data_dir):
    sid = new_scene_id("beach_chat", "duo")
    scene = Scene(id=sid, situation="beach_chat", name="二人+一人",
                  cast={"left": "elesa_bw", "center": "skyla_bw", "right": "hex_maniac_xy"},
                  outfits={"center": "none", "left": None})
    r = compile_scene(repo, scene)
    path = save_scene(repo, scene, r)
    assert path == data_dir / "situations" / "beach" / "beach_chat__duo.yaml"
    repo2 = Repository(data_dir)
    s2 = repo2.scene(sid)
    assert s2.cast == scene.cast and s2.outfits == {"center": "none"} and s2.name == "二人+一人"
    assert compile_scene(repo2, s2).moss_prompt == r.moss_prompt
    with pytest.raises(ValueError):  # 既存への新規作成は拒否（repo は古いので path 未登録）
        save_scene(repo, scene, r)


def test_save_refuses_when_errors(repo, trio):
    bad = replace(trio, cast={"left": "skyla_bw"})
    with pytest.raises(ValueError):
        save_scene(repo, bad, compile_scene(repo, bad))


def test_new_scene_id_validation():
    assert new_scene_id("beach_chat", "trio2") == "beach_chat__trio2"
    for bad in ("", "Has Space", "日本語"):
        with pytest.raises(ValueError):
            new_scene_id("beach_chat", bad)


# ---- 生成結果の記録（compiled.seed / compiled.images）----
def _compiled(path):
    from moa.core.repository import load_yaml
    return load_yaml(path)["compiled"]


def test_save_writes_generation_record(repo, trio):
    from moa.core.scene_io import GenerationRecord
    path = repo.path_of("scene", trio.id)
    save_scene(repo, trio, compile_scene(repo, trio),
               generation=GenerationRecord(777, ["output/moss_1.png", "output/anima_1.png"]))
    c = _compiled(path)
    assert c["seed"] == 777 and c["images"] == ["output/moss_1.png", "output/anima_1.png"]


def test_save_resets_seed_and_images_when_content_changes(repo, trio):
    from moa.core.scene_io import GenerationRecord
    path = repo.path_of("scene", trio.id)
    save_scene(repo, trio, compile_scene(repo, trio), generation=GenerationRecord(5, ["a.png"]))

    # 内容が同じなら、生成なしで保存し直しても残る
    save_scene(repo, trio, compile_scene(repo, trio))
    assert _compiled(path)["seed"] == 5 and _compiled(path)["images"] == ["a.png"]

    # キャストを変えて保存すると、画像と内容が食い違うのでリセットされる
    changed = replace(trio, cast={**trio.cast, "center": "skyla_bw"})
    save_scene(repo, changed, compile_scene(repo, changed))
    assert _compiled(path)["seed"] is None and _compiled(path)["images"] == []

    # ネガティブ適用の切り替えも同様
    save_scene(repo, changed, compile_scene(repo, changed), generation=GenerationRecord(6, ["b.png"]))
    flipped = replace(changed, apply_negative=True)
    save_scene(repo, flipped, compile_scene(repo, flipped))
    assert _compiled(path)["seed"] is None and _compiled(path)["images"] == []


# ---- 生成パラメータ（gen）----
GEN_OK = "gen:\n  moss:  { steps: 50, cfg: 3.0 }\n  anima: { steps: 14, cfg: 1.8, denoise: 0.45 }"


def _with_gen(data_dir, block):
    path = data_dir / "situations" / "beach" / "beach_chat__trio.yaml"
    edit(path, "applyNegative: false", "applyNegative: false\n" + block)
    return path


def test_scene_gen_is_loaded(data_dir):
    _with_gen(data_dir, GEN_OK)
    repo2 = Repository(data_dir)
    assert not errors(repo2.issues)
    assert repo2.scene("beach_chat__trio").gen == {"moss": {"steps": 50, "cfg": 3.0},
                                                   "anima": {"steps": 14, "cfg": 1.8, "denoise": 0.45}}


@pytest.mark.parametrize("block,keyword", [
    ("gen:\n  moss: { denoise: 0.5 }", "denoise"),          # MOSS側の denoise はエラー
    ("gen:\n  moss: { steps: 0 }", "steps"),
    ("gen:\n  moss: { steps: 12.5 }", "steps"),
    ("gen:\n  anima: { cfg: 0 }", "cfg"),
    ("gen:\n  anima: { cfg: -1.0 }", "cfg"),
    ("gen:\n  anima: { denoise: 1.5 }", "denoise"),
    ("gen:\n  anima: { denoise: -0.1 }", "denoise"),
    ("gen:\n  other: { steps: 5 }", "other"),
    ("gen:\n  anima: { sampler: euler }", "sampler"),
])
def test_scene_gen_schema_violations(data_dir, block, keyword):
    _with_gen(data_dir, block)
    repo2 = Repository(data_dir)
    assert repo2.scene("beach_chat__trio") is None
    assert any("gen" in i.message and keyword in i.message for i in errors(repo2.issues)), repo2.issues


def test_scene_gen_boundary_values_are_valid(data_dir):
    _with_gen(data_dir, "gen:\n  moss: { steps: 1, cfg: 0.01 }\n  anima: { denoise: 0 }")
    assert not errors(Repository(data_dir).issues)


def test_legacy_denoise_key_maps_to_gen_anima_denoise(data_dir):
    path = _with_gen(data_dir, "denoise: 0.6")
    assert Repository(data_dir).scene("beach_chat__trio").gen == {"anima": {"denoise": 0.6}}
    edit(path, "denoise: 0.6", "denoise: 0.6\ngen:\n  anima: { denoise: 0.45 }")
    assert Repository(data_dir).scene("beach_chat__trio").gen == {"anima": {"denoise": 0.45}}  # gen が優先


def test_save_writes_gen_in_flow_style_and_migrates_legacy_denoise(repo, data_dir):
    from moa.core.repository import load_yaml
    path = _with_gen(data_dir, "denoise: 0.6")
    repo.reload()
    sc = replace(repo.scene("beach_chat__trio"),
                 gen={"anima": {"denoise": 0.45, "steps": 14}, "moss": {"cfg": 3.0}})
    save_scene(repo, sc, compile_scene(repo, sc))

    data = load_yaml(path)
    assert "denoise" not in data                                           # 旧キーは消える
    assert data["gen"] == {"moss": {"cfg": 3.0}, "anima": {"steps": 14, "denoise": 0.45}}
    assert list(data["gen"]) == ["moss", "anima"] and list(data["gen"]["anima"]) == ["steps", "denoise"]
    assert "moss: {" in path.read_text(encoding="utf-8")                    # 段ごとに1行

    save_scene(repo, replace(sc, gen={}), compile_scene(repo, sc))          # 空なら gen を消す
    assert "gen" not in load_yaml(path)


def test_compiled_gen_is_recorded_and_reset_with_the_snapshot(repo):
    from moa.core.scene_io import GenerationRecord
    path = repo.path_of("scene", "beach_chat__trio")
    used = {"moss": {"steps": 50, "cfg": 5}, "anima": {"steps": 30, "cfg": 2, "denoise": 0.35}}
    sc = repo.scene("beach_chat__trio")
    save_scene(repo, sc, compile_scene(repo, sc), generation=GenerationRecord(9, ["a.png"], used))
    assert _compiled(path)["gen"] == used and _compiled(path)["seed"] == 9

    # Scene.gen を変えただけでは消えない（compiled.gen は「生成時に使った値」の記録）
    changed_gen = replace(sc, gen={"anima": {"steps": 99}})
    save_scene(repo, changed_gen, compile_scene(repo, changed_gen))
    c = _compiled(path)
    assert c["gen"] == used and c["images"] == ["a.png"]

    # プロンプトが変わると、画像とあわせてリセットされる
    changed_cast = replace(sc, cast={**sc.cast, "center": "skyla_bw"})
    save_scene(repo, changed_cast, compile_scene(repo, changed_cast))
    c = _compiled(path)
    assert "gen" not in c and c["seed"] is None and c["images"] == []


def test_legacy_compiled_denoise_is_dropped_on_save(repo, data_dir):
    path = repo.path_of("scene", "beach_chat__trio")
    edit(path, "compiled:", "compiled:\n  denoise: 0.35")
    repo.reload()
    assert not errors(repo.issues)                                          # 旧キーも読み込める
    sc = repo.scene("beach_chat__trio")
    save_scene(repo, sc, compile_scene(repo, sc))
    assert "denoise" not in _compiled(path)
