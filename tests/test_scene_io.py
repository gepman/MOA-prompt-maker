"""Scene の保存（9.1章、7.6章、13.4章の記録規則）。"""
from dataclasses import replace

import pytest

from conftest import edit, errors, scene_of, write
from moa.core import CastEntry, Repository, compile_scene, save_scene, validate_scene_id
from moa.core.repository import load_yaml
from moa.core.scene_io import GenerationRecord

USED = {"moss": {"steps": 50, "cfg": 5}, "anima": {"steps": 30, "cfg": 2, "denoise": 0.35}}


def compiled_of(path):
    return load_yaml(path)["compiled"]


# ---- 既存の Scene を in-place で更新する ----
def test_existing_scene_is_updated_in_place_keeping_comments_and_order(repo, data_dir):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    cast = {**scene.cast, "right": CastEntry("hex_maniac_xy", "skyla_pilot"),       # outfit を追加
            "center": CastEntry("elesa_bw", None)}                                  # outfit を削除（委譲に戻す）
    changed = replace(scene, cast=cast)
    save_scene(repo, changed, compile_scene(repo, changed))

    text = path.read_text(encoding="utf-8")
    assert "# outfit 省略 → Action 指定なし → defaultOutfit (skyla_pilot) が効く" in text      # left のコメント
    data = load_yaml(path)
    assert list(data)[:5] == ["id", "name", "background", "action", "cast"]
    assert data["cast"]["center"] == {"character": "elesa_bw"}
    assert data["cast"]["right"] == {"character": "hex_maniac_xy", "outfit": "skyla_pilot"}
    assert data["gen"] == {"moss": {"steps": 50, "cfg": 3.0}, "anima": {"steps": 14, "cfg": 1.8, "denoise": 0.45}}
    assert not errors(Repository(data_dir).issues)


def test_scene_with_no_gen_stays_without_gen_and_keeps_its_comment(repo):
    path = repo.path_of("scene", "beach_solo__elesa")
    scene = repo.scene("beach_solo__elesa")
    save_scene(repo, scene, compile_scene(repo, scene))
    text = path.read_text(encoding="utf-8")
    assert "# gen 省略 → ワークフローのノード値がそのまま使われる" in text
    assert "\ngen:" not in text


def test_gen_is_written_one_line_per_stage_and_removed_when_empty(repo):
    path = repo.path_of("scene", "beach_solo__elesa")
    scene = replace(repo.scene("beach_solo__elesa"), gen={"anima": {"denoise": 0.45, "steps": 14}, "moss": {"cfg": 3.0}})
    save_scene(repo, scene, compile_scene(repo, scene))
    assert "moss: {cfg: 3.0}" in path.read_text(encoding="utf-8")
    assert load_yaml(path)["gen"] == {"moss": {"cfg": 3.0}, "anima": {"steps": 14, "denoise": 0.45}}

    cleared = replace(scene, gen={})
    save_scene(repo, cleared, compile_scene(repo, cleared))
    assert "gen" not in load_yaml(path)


def test_name_is_kept_when_the_scene_has_none(repo):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = replace(repo.scene("beach_chat__trio"), name=None)
    save_scene(repo, scene, compile_scene(repo, scene))
    assert load_yaml(path)["name"] == "海辺で談笑（ポケモントリオ）"


# ---- 新規 Scene ----
def test_new_scene_is_created_in_the_chosen_folder(repo, data_dir):
    scene = scene_of("beach_solo", {"solo": ("skyla_bw", "none")}, id="my_new", name="新しい")
    folder = data_dir / "scenes" / "pokemon" / "summer"
    path = save_scene(repo, scene, compile_scene(repo, scene), folder=folder)

    assert path == folder.resolve() / "my_new.yaml"
    data = load_yaml(path)
    assert list(data) == ["id", "name", "background", "action", "cast", "applyNegative", "compiled"]
    assert data["cast"] == {"solo": {"character": "skyla_bw", "outfit": "none"}}
    assert list(data["compiled"]) == ["background", "action", "canvas", "rating", "mossPrompt", "animaPrompt",
                                      "negativeApplied", "seed", "images"]
    assert data["compiled"]["seed"] is None and data["compiled"]["images"] == []
    assert "canvas: {w: 1536, h: 1024}" in path.read_text(encoding="utf-8")

    repo2 = Repository(data_dir)
    assert errors(repo2.issues) == [] and repo2.scene("my_new").name == "新しい"
    assert compile_scene(repo2, repo2.scene("my_new")).moss_prompt == compile_scene(repo, scene).moss_prompt


def test_new_scene_defaults_to_the_scenes_root(repo, data_dir):
    scene = scene_of("beach_solo", {"solo": "elesa_bw"}, id="root_level")
    assert save_scene(repo, scene, compile_scene(repo, scene)) == data_dir.resolve() / "scenes" / "root_level.yaml"


@pytest.mark.parametrize("where", ["characters", "../outside", ".."])
def test_new_scene_must_be_under_the_scenes_root(repo, data_dir, where):
    scene = scene_of("beach_solo", {"solo": "elesa_bw"}, id="x")
    with pytest.raises(ValueError, match="scenes/ 配下"):
        save_scene(repo, scene, compile_scene(repo, scene), folder=data_dir / where)
    assert not (data_dir / "characters" / "x.yaml").exists()


def test_new_scene_refuses_to_overwrite_an_unindexed_file(repo, data_dir):
    write(data_dir / "scenes" / "taken.yaml", "keep me")
    scene = scene_of("beach_solo", {"solo": "elesa_bw"}, id="taken")
    with pytest.raises(ValueError, match="既に存在"):
        save_scene(repo, scene, compile_scene(repo, scene))
    assert (data_dir / "scenes" / "taken.yaml").read_text(encoding="utf-8") == "keep me"


def test_scene_with_errors_is_not_saved(repo, data_dir):
    scene = scene_of("beach_solo", {"solo": "nobody"}, id="broken")
    with pytest.raises(ValueError, match="検証エラー"):
        save_scene(repo, scene, compile_scene(repo, scene))
    assert not (data_dir / "scenes" / "broken.yaml").exists()


def test_no_temp_file_is_left_behind(repo, data_dir):
    scene = repo.scene("beach_chat__trio")
    save_scene(repo, scene, compile_scene(repo, scene))
    assert not list(data_dir.rglob("*.tmp"))


@pytest.mark.parametrize("sid,ok", [("a_b1", True), ("A", False), ("a-b", False), ("日本語", False), ("", False), ("a b", False)])
def test_scene_id_validation(sid, ok):
    if ok:
        assert validate_scene_id(f" {sid} ") == sid
    else:
        with pytest.raises(ValueError):
            validate_scene_id(sid)


# ---- 生成結果の記録（13.4章）----
def test_generation_record_is_written(repo):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    save_scene(repo, scene, compile_scene(repo, scene),
               generation=GenerationRecord(777, ["output/m.png", "output/a.png"], USED))
    c = compiled_of(path)
    assert c["seed"] == 777 and c["images"] == ["output/m.png", "output/a.png"] and c["gen"] == USED
    assert "partial" not in c


def test_partial_result_is_marked_and_cleared_by_the_next_full_save(repo):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    r = compile_scene(repo, scene)
    save_scene(repo, scene, r, generation=GenerationRecord(5, ["output/m.png"], USED, partial=True))
    c = compiled_of(path)
    assert c["partial"] is True and c["images"] == ["output/m.png"]

    save_scene(repo, scene, r, generation=GenerationRecord(5, ["output/m.png", "output/a.png"], USED))
    c = compiled_of(path)
    assert "partial" not in c and len(c["images"]) == 2


def test_unchanged_content_keeps_the_recorded_result(repo):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    r = compile_scene(repo, scene)
    save_scene(repo, scene, r, generation=GenerationRecord(9, ["a.png"], USED, partial=True))
    save_scene(repo, scene, r)                                           # 生成なしで保存し直す
    c = compiled_of(path)
    assert (c["seed"], c["images"], c["gen"], c["partial"]) == (9, ["a.png"], USED, True)


def test_scene_gen_changes_do_not_reset_the_record(repo):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    r = compile_scene(repo, scene)
    save_scene(repo, scene, r, generation=GenerationRecord(9, ["a.png"], USED))
    moved = replace(scene, gen={"anima": {"steps": 99}})
    save_scene(repo, moved, compile_scene(repo, moved))
    c = compiled_of(path)
    assert c["gen"] == USED and c["images"] == ["a.png"]                 # compiled.gen は生成時の記録


@pytest.mark.parametrize("change", ["cast", "negative"])
def test_changed_content_resets_everything_recorded(repo, change):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    save_scene(repo, scene, compile_scene(repo, scene),
               generation=GenerationRecord(9, ["m.png"], USED, partial=True))
    changed = replace(scene, cast={**scene.cast, "center": CastEntry("skyla_bw", "none")}) if change == "cast" \
        else replace(scene, apply_negative=True)
    save_scene(repo, changed, compile_scene(repo, changed))
    c = compiled_of(path)
    assert c["seed"] is None and c["images"] == [] and "gen" not in c and "partial" not in c


def test_snapshot_tracks_the_background_and_rating(repo, data_dir):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = replace(repo.scene("beach_chat__trio"), background="white_background")
    save_scene(repo, scene, compile_scene(repo, scene))
    c = compiled_of(path)
    assert (c["background"], c["action"], c["rating"]) == ("white_background", "beach_chat", "safe")
    assert load_yaml(path)["background"] == "white_background"
