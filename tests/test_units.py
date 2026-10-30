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
