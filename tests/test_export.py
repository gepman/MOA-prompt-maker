"""エクスポート（SPEC 10.3章）：閉包・フォルダ一括・zip の中身・_folder.yaml・生成の記録・中止・画面ロジック。"""
import io
import shutil
import zipfile

from ruamel.yaml import YAML

from conftest import edit, write
from moa.core import Repository
from moa.core.export import export_folder, export_scene
from moa.ui import exportblock as EB
from moa.ui import logic as L

TRIO = "beach_chat__trio"
COMPILED = """compiled:   # スナップショット
  background: beach
  action: beach_chat
  canvas: { w: 1536, h: 1024 }
  rating: safe
  mossPrompt: moss text
  animaPrompt: anima text
  negativeApplied: false
  gen: { moss: { steps: 50, cfg: 3.0 } }   # 生成時の値
  seed: 123
  images:
    - output/a.png
  partial: true
"""


def names(res):
    return set(zipfile.ZipFile(io.BytesIO(res.zip_bytes)).namelist())


def read(res, name):
    return zipfile.ZipFile(io.BytesIO(res.zip_bytes)).read(name)


def load_text(text):
    return YAML(typ="safe").load(text)


def add_compiled(data_dir, scene_id=TRIO):
    path = data_dir / "scenes" / f"{scene_id}.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.rstrip("\n") + "\n" + COMPILED, encoding="utf-8", newline="\n")
    return path


def export_folder_of(data_dir, order, strip=True):
    write(data_dir / "scenes" / "_folder.yaml", "order:\n" + "".join(f"  - {n}\n" for n in order))
    return export_folder(data_dir, data_dir / "scenes", strip)


# ---- 閉包 ----
def test_closure_collects_every_dependency_once_and_never_none(data_dir):
    res = export_scene(data_dir, TRIO)
    assert res.ok, res.problems
    # center の outfit: none は含めない。left / right は defaultOutfit、elesa_bw の defaultOutfit は none ではなく elesa_bw_summer
    assert names(res) == {
        "scenes/beach_chat__trio.yaml", "backgrounds/beach.yaml", "actions/beach_chat.yaml",
        "characters/skyla_bw.yaml", "characters/elesa_bw.yaml", "characters/hex_maniac_xy.yaml",
        "outfits/skyla_pilot.yaml", "outfits/elesa_bw_summer.yaml", "outfits/hex_summer.yaml"}
    assert not any("none" in n for n in names(res))
    assert res.counts == {"scene": 1, "action": 1, "background": 1, "character": 3, "outfit": 3}


def test_action_outfits_and_recommended_background_are_included(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "background: beach", "background: white_background")
    edit(data_dir / "actions" / "beach_chat.yaml", "  center: elesa_bw_summer", "  center: elesa_bw_summer\n  left: elesa_sling")
    res = export_scene(data_dir, TRIO)
    assert res.ok, res.problems
    assert "backgrounds/white_background.yaml" in names(res)   # 推奨背景（Scene の背景は beach のまま）
    assert "backgrounds/beach.yaml" in names(res)
    assert "outfits/elesa_sling.yaml" in names(res)            # Scene で上書きされていても、参照されていれば入る


def test_a_shared_entity_is_included_once(data_dir):
    res = export_folder_of(data_dir, ["beach_solo__elesa.yaml", "beach_wave_boy__elesa.yaml"])
    assert res.ok, res.problems
    assert res.counts["scene"] == 2 and res.counts["background"] == 1
    assert len([n for n in names(res) if n.startswith("backgrounds/")]) == 1


# ---- フォルダ一括 ----
def test_folder_export_takes_only_the_direct_scenes_listed_in_order(data_dir):
    sub = data_dir / "scenes" / "sub"
    sub.mkdir()
    shutil.move(str(data_dir / "scenes" / "guilty_lineup__trio.yaml"), sub / "guilty_lineup__trio.yaml")
    res = export_folder_of(data_dir, [f"{TRIO}.yaml", "sub", "ghost.yaml"])   # beach_solo__elesa は order に無い
    assert res.ok, res.problems
    assert {n for n in names(res) if n.startswith("scenes/")} == {"scenes/beach_chat__trio.yaml", "scenes/_folder.yaml"}
    assert res.scenes == [("海辺で談笑（ポケモントリオ）", TRIO)]
    assert res.filename == "scenes_export.zip"


def test_folder_export_filename_uses_the_real_folder_name_not_the_display_name(data_dir):
    sub = data_dir / "scenes" / "trip"
    sub.mkdir()
    shutil.move(str(data_dir / "scenes" / f"{TRIO}.yaml"), sub / f"{TRIO}.yaml")
    write(sub / "_folder.yaml", "name: 旅行\norder:\n  - beach_chat__trio.yaml\n")
    res = export_folder(data_dir, sub)
    assert res.ok and res.filename == "trip_export.zip"
    assert f"scenes/trip/{TRIO}.yaml" in names(res)


def test_zero_targets_abort_with_a_reason(data_dir):
    scenes = data_dir / "scenes"
    res = export_folder(data_dir, scenes)                         # _folder.yaml が無い
    assert not res.ok and res.zip_bytes is None and "_folder.yaml がありません" in res.problems[0]
    write(scenes / "_folder.yaml", "name: x\n")                   # order が無い
    assert "order がありません" in export_folder(data_dir, scenes).problems[0]
    write(scenes / "_folder.yaml", "order: []\n")                 # 空
    assert "order がありません" in export_folder(data_dir, scenes).problems[0]
    write(scenes / "_folder.yaml", "order:\n  - sub\n  - ghost.yaml\n")   # 直下の Scene が1つも無い
    res = export_folder(data_dir, scenes)
    assert not res.ok and res.zip_bytes is None and "直下の Scene がありません" in res.problems[0]


# ---- zip の中身 ----
def test_paths_start_at_the_kind_folder_and_keep_the_original_subfolders(data_dir):
    (data_dir / "characters" / "pokemon").mkdir()
    shutil.move(str(data_dir / "characters" / "elesa_bw.yaml"), data_dir / "characters" / "pokemon" / "elesa_bw.yaml")
    res = export_scene(data_dir, TRIO)
    assert res.ok, res.problems
    assert "characters/pokemon/elesa_bw.yaml" in names(res)
    assert all(n.split("/")[0] in {"actions", "scenes", "backgrounds", "characters", "outfits"} for n in names(res))


def test_non_scene_files_are_byte_identical(data_dir):
    crlf = data_dir / "outfits" / "skyla_pilot.yaml"
    crlf.write_bytes(crlf.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))   # 改行も加工しない
    res = export_scene(data_dir, TRIO)
    for name in names(res):
        if name.startswith("scenes/") or name.endswith("_folder.yaml"):
            continue
        assert read(res, name) == (data_dir / name).read_bytes(), name


# ---- _folder.yaml ----
def test_folder_yaml_is_included_with_a_trimmed_order_and_nothing_else_changed(data_dir):
    write(data_dir / "characters" / "_folder.yaml",
          "# キャラ棚\nname: 人物   # 表示名\ndescription: 説明\norder:\n  - skyla_bw.yaml   # 先頭\n"
          "  - skyla_guilty.yaml\n  - elesa_bw.yaml\n  - nobody.yaml\n")
    res = export_scene(data_dir, TRIO)
    assert res.ok, res.problems
    text = read(res, "characters/_folder.yaml").decode("utf-8")
    assert "skyla_guilty" not in text and "nobody" not in text
    assert "# キャラ棚" in text and "# 表示名" in text and "# 先頭" in text and "説明" in text
    assert load_text(text)["order"] == ["skyla_bw.yaml", "elesa_bw.yaml"]
    assert load_text(text)["name"] == "人物"


def test_untouched_folder_yaml_is_copied_as_is_and_intermediate_levels_are_included(data_dir):
    (data_dir / "characters" / "a" / "b").mkdir(parents=True)
    shutil.move(str(data_dir / "characters" / "elesa_bw.yaml"), data_dir / "characters" / "a" / "b" / "elesa_bw.yaml")
    (data_dir / "characters" / "a" / "_folder.yaml").write_bytes(b"name: A\r\norder:\r\n  - b\r\n")
    (data_dir / "characters" / "a" / "b" / "_folder.yaml").write_bytes(b"order:\n  - elesa_bw.yaml\n")
    res = export_scene(data_dir, TRIO)
    assert read(res, "characters/a/_folder.yaml") == b"name: A\r\norder:\r\n  - b\r\n"
    assert read(res, "characters/a/b/_folder.yaml") == b"order:\n  - elesa_bw.yaml\n"


# ---- 生成の記録 ----
def test_generation_record_is_removed_but_snapshot_and_comments_stay(data_dir):
    add_compiled(data_dir)
    res = export_scene(data_dir, TRIO, strip_generation=True)
    out = read(res, f"scenes/{TRIO}.yaml").decode("utf-8")
    compiled = load_text(out)["compiled"]
    assert set(compiled) == {"background", "action", "canvas", "rating", "mossPrompt", "animaPrompt", "negativeApplied"}
    assert compiled["mossPrompt"] == "moss text"
    assert "# スナップショット" in out and "# outfit 省略 → Action 指定なし" in out
    assert load_text(out)["gen"]["moss"] == {"steps": 50, "cfg": 3.0}    # Scene 自身の gen は残る
    assert "images" not in out and "seed" not in out and "partial" not in out
    assert res.notes == []


def test_generation_record_kept_when_the_check_is_off_and_scene_is_byte_identical(data_dir):
    path = add_compiled(data_dir)
    res = export_scene(data_dir, TRIO, strip_generation=False)
    assert read(res, f"scenes/{TRIO}.yaml") == path.read_bytes()
    assert "画像のパスは、このPCの中の場所です" in res.notes


def test_a_scene_without_generation_record_is_byte_identical_even_when_stripping(data_dir):
    res = export_scene(data_dir, TRIO, strip_generation=True)
    assert read(res, f"scenes/{TRIO}.yaml") == (data_dir / "scenes" / f"{TRIO}.yaml").read_bytes()


# ---- 中止 ----
def test_missing_dependency_aborts_without_a_zip_and_names_source_and_target(data_dir):
    (data_dir / "outfits" / "skyla_pilot.yaml").unlink()
    res = export_scene(data_dir, TRIO)
    assert not res.ok and res.zip_bytes is None
    text = "\n".join(res.problems)
    assert "Character `skyla_bw` の defaultOutfit" in text and "Outfit `skyla_pilot`" in text


def test_missing_recommended_background_aborts(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "background: beach", "background: nowhere")
    res = export_scene(data_dir, TRIO)
    assert not res.ok and "Action `beach_chat` の background（推奨背景）" in "\n".join(res.problems)


def test_duplicate_id_aborts_and_names_kind_id_and_both_files(data_dir):
    (data_dir / "outfits" / "dup").mkdir()
    shutil.copy(data_dir / "outfits" / "hex_summer.yaml", data_dir / "outfits" / "dup" / "hex_summer.yaml")
    res = export_scene(data_dir, TRIO)
    assert not res.ok and res.zip_bytes is None
    text = "\n".join(res.problems)
    assert "Outfit `hex_summer`" in text and "outfits/hex_summer.yaml" in text and "outfits/dup/hex_summer.yaml" in text


def test_a_duplicate_that_is_not_a_dependency_does_not_abort(data_dir):
    (data_dir / "outfits" / "dup").mkdir()
    shutil.copy(data_dir / "outfits" / "guilty_suit_blue.yaml", data_dir / "outfits" / "dup" / "guilty_suit_blue.yaml")
    assert export_scene(data_dir, TRIO).ok


def test_unknown_scene_aborts(data_dir):
    assert not export_scene(data_dir, "nope").ok


def test_export_does_not_touch_the_library(data_dir):
    before = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    export_scene(data_dir, TRIO)
    assert {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()} == before


# ---- 画面ロジック ----
def opened(repo, scene_id=TRIO):
    nav = L.set_target(L.new_nav(), "scene")
    sc, _, _ = L.apply_entry(repo, L.new_sc(), nav, repo.entry("scene", scene_id))
    return sc


def test_button_is_unavailable_for_an_unsaved_scene(repo):
    assert EB.scene_entry(repo, L.new_sc()) is None
    nav = L.set_target(L.new_nav(), "action")
    sc, _, _ = L.apply_entry(repo, L.new_sc(), nav, repo.entry("action", "beach_chat"))
    assert EB.scene_entry(repo, sc) is None           # 新規作成中
    assert EB.scene_entry(repo, opened(repo)) is not None


def test_screen_changes_after_saving_are_flagged_and_unchanged_screens_are_not(repo):
    sc = opened(repo)
    gp = L.sync_gp(sc, L.new_gp())
    assert not EB.screen_changed(repo, sc, gp["gen"], "")
    assert not EB.screen_changed(repo, sc, gp["gen"], sc["scene_name"])
    assert EB.screen_changed(repo, L.set_outfit(sc, "left", "none"), gp["gen"], "")
    assert EB.screen_changed(repo, sc, gp["gen"], "別の名前")
    assert EB.screen_changed(repo, sc, L.set_override(gp, "moss", "steps", 20)["gen"], "")
    assert EB.screen_changed(repo, dict(sc, apply_negative=True), gp["gen"], "")
    assert not EB.screen_changed(repo, L.new_sc(), {}, "")      # 未保存は比べない


def test_result_text_shows_scenes_counts_notes_and_reasons(data_dir):
    ok = export_scene(data_dir, TRIO, strip_generation=False)
    text = EB.result_text(ok, changed=True)
    assert EB.CHANGED_NOTE in text and TRIO in text and "Character 3 件" in text and "このPCの中の場所" in text
    (data_dir / "outfits" / "skyla_pilot.yaml").unlink()
    bad = export_scene(data_dir, TRIO)
    assert EB.write_zip(bad) is None and "中止" in EB.result_text(bad) and "skyla_pilot" in EB.result_text(bad)


def test_write_zip_keeps_the_default_file_name(data_dir):
    path = EB.write_zip(export_scene(data_dir, TRIO))
    assert path.replace("\\", "/").endswith(f"/{TRIO}_export.zip")
    assert zipfile.is_zipfile(path)


def test_exported_zip_loads_as_a_library(data_dir, tmp_path):
    res = export_scene(data_dir, TRIO)
    out = tmp_path / "lib"
    zipfile.ZipFile(io.BytesIO(res.zip_bytes)).extractall(out)
    repo2 = Repository(out)
    assert not [i for i in repo2.issues if i.level == "error"], repo2.issues
    assert repo2.scene(TRIO) is not None
