"""9.2章：意味チェックの表を、1行ずつ確認する。"""
from dataclasses import replace

import pytest

from conftest import compile_sample, edit, errors, make_action, make_character, scene_of, warnings
from moa.core import CastEntry, Repository, compile_scene


def msgs(issues):
    return [i.message for i in issues]


# ---- 参照先が存在しない ----
@pytest.mark.parametrize("path,old,new,fragment", [
    ("characters/skyla_bw.yaml", "defaultOutfit: skyla_pilot", "defaultOutfit: nope", "defaultOutfit 'nope'"),
    ("actions/beach_chat.yaml", "center: elesa_bw_summer", "center: nope", "outfits[center] の服装 'nope'"),
    ("scenes/beach_chat__trio.yaml", "background: beach", "background: nope", "Background 'nope'"),
    ("scenes/beach_chat__trio.yaml", "action: beach_chat", "action: nope", "Action 'nope'"),
    ("scenes/beach_chat__trio.yaml", "character: skyla_bw", "character: nope", "キャラクター 'nope'"),
    ("scenes/beach_chat__trio.yaml", "outfit: none", "outfit: nope", "服装 'nope'"),
])
def test_missing_references_are_errors(data_dir, path, old, new, fragment):
    edit(data_dir / path, old, new)
    repo = Repository(data_dir)
    assert any(fragment in m for m in msgs(errors(repo.issues))), repo.issues
    r = compile_sample(repo, "beach_chat__trio") if not path.startswith("scenes") else \
        compile_scene(repo, repo.scene("beach_chat__trio"))
    assert not r.ok and r.moss_prompt == "" and r.anima_prompt == ""


def test_default_outfit_none_and_action_outfit_none_are_valid(data_dir):
    edit(data_dir / "characters" / "elesa_bw.yaml", "defaultOutfit: elesa_bw_summer", "defaultOutfit: none")
    edit(data_dir / "actions" / "beach_chat.yaml", "center: elesa_bw_summer", "center: none")
    assert errors(Repository(data_dir).issues) == []


def test_action_recommended_background_missing_is_only_a_warning(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "background: beach", "background: nope")
    repo = Repository(data_dir)
    assert errors(repo.issues) == []
    assert any("推奨背景 'nope'" in m for m in msgs(warnings(repo.issues)))
    r = compile_sample(repo, "beach_chat__trio")
    assert r.ok and r.moss_prompt != ""                                  # 警告は出力と一緒に返る
    assert any("推奨背景" in m for m in msgs(warnings(r.issues)))


def test_a_scene_does_not_need_the_recommended_background(repo):
    r = compile_sample(repo, "beach_chat__trio", background="white_background")   # 推奨と違う背景を選べる
    assert r.ok and r.canvas == {"w": 1536, "h": 1024}
    assert "white background, simple background" in r.anima_prompt and "beach, ocean" not in r.anima_prompt


# ---- cast と slots の過不足 ----
def test_cast_must_match_the_slots_exactly(repo, trio):
    r = compile_scene(repo, replace(trio, cast={k: v for k, v in trio.cast.items() if k != "right"}))
    assert not r.ok and any("スロット 'right' にキャラクターが割り当てられていません" in m for m in msgs(errors(r.issues)))

    r = compile_scene(repo, replace(trio, cast={**trio.cast, "bystander": CastEntry("skyla_bw")}))
    assert not r.ok and any("宣言されていないスロット 'bystander'" in m for m in msgs(errors(r.issues)))


def test_action_outfits_with_an_undeclared_slot_is_an_error(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "outfits:\n  center: elesa_bw_summer",
         "outfits:\n  center: elesa_bw_summer\n  ghost: none")
    repo = Repository(data_dir)
    assert any("outfits に宣言されていないスロット 'ghost'" in m for m in msgs(errors(repo.issues)))


# ---- プレイスホルダー ----
@pytest.mark.parametrize("bad", ["{{left.foo}}", "{{left.bodyhair}}", "{{left.Outfit}}", "{{left.outfit.x}}", "{{.outfit}}", "{{ }}"])
@pytest.mark.parametrize("field", ["mossCaption", "mossItems", "anima"])
def test_malformed_placeholders_are_errors_in_every_field(data_dir, bad, field):
    path = data_dir / "actions" / "beach_chat.yaml"
    if field == "mossCaption":
        edit(path, "  Three adult women stand", f"  {bad} Three adult women stand")
        label = "mossCaption"
    elif field == "mossItems":
        edit(path, "She laughs with her mouth open", f"{bad} She laughs with her mouth open")
        label = "mossItems[1].caption"
    else:
        edit(path, "  {{left}}, {{center}}", f"  {bad}, {{{{left}}}}, {{{{center}}}}")
        label = "anima"
    repo = Repository(data_dir)
    assert any("不正なプレイスホルダー" in m and label in m for m in msgs(errors(repo.issues))), (bad, field, repo.issues)
    assert not compile_sample(repo, "beach_chat__trio").ok


@pytest.mark.parametrize("field", ["mossCaption", "mossItems", "anima"])
def test_undeclared_slots_are_errors_in_every_field(data_dir, field):
    path = data_dir / "actions" / "beach_chat.yaml"
    if field == "mossCaption":
        edit(path, "  Three adult women stand", "  {{ghost}} Three adult women stand")
    elif field == "mossItems":
        edit(path, "She laughs with her mouth open", "{{ghost.outfit}} She laughs with her mouth open")
    else:
        edit(path, "  {{left}}, {{center}}", "  {{ghost.bodyHair}}, {{left}}, {{center}}")
    repo = Repository(data_dir)
    assert any("宣言されていないスロット 'ghost'" in m for m in msgs(errors(repo.issues))), repo.issues


def test_placeholder_forms_inner_spaces_and_repeats_are_fine(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "  Three adult women stand",
         "  {{ left }} {{center}} {{ right.outfit }} Three adult women stand")
    repo = Repository(data_dir)
    assert errors(repo.issues) == []
    caption = compile_sample(repo, "beach_chat__trio").moss_prompt
    assert caption.count("Skyla as she appears") == 2          # mossCaption と mossItems の両方に展開される


# ---- 未使用スロット ----
@pytest.mark.parametrize("where", ["mossCaption", "mossItem", "anima", "bodyHair"])
def test_a_slot_used_anywhere_is_not_unused(data_dir, where):
    path = data_dir / "actions" / "beach_chat.yaml"
    edit(path, "slots: [left, center, right]", "slots: [left, center, right, extra]")
    repo = Repository(data_dir)
    assert any("スロット 'extra'" in m and "使われていません" in m for m in msgs(warnings(repo.issues)))   # 対照

    if where == "mossCaption":
        edit(path, "  Three adult women stand", "  {{extra}} Three adult women stand")
    elif where == "mossItem":
        edit(path, "She laughs with her mouth open", "{{extra}} She laughs with her mouth open")
    elif where == "anima":
        edit(path, "  {{left}}, {{center}}", "  {{extra}}, {{left}}, {{center}}")
    else:
        edit(path, "She laughs with her mouth open", "{{extra.bodyHair}} She laughs with her mouth open")
    assert not any("'extra'" in m and "使われていません" in m for m in msgs(warnings(Repository(data_dir).issues)))


def test_unused_slot_is_only_a_warning_and_the_scene_still_compiles(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "slots: [left, center, right]", "slots: [left, center, right, extra]")
    repo = Repository(data_dir)
    scene = repo.scene("beach_chat__trio")
    r = compile_scene(repo, replace(scene, cast={**scene.cast, "extra": CastEntry("skyla_bw")}))
    assert r.ok and r.moss_prompt and any("使われていません" in i.message for i in r.issues)


# ---- 重複と id・ファイル名は test_repository.py。人数タグ・bbox・Background のプレイスホルダーは test_acceptance.py ----
def test_warnings_do_not_block_and_errors_always_do(data_dir):
    make_character(data_dir, "c1")
    make_action(data_dir, "act", ["s"], anima="3girls, {{s}}")                         # 人数タグ → 警告だけ
    repo = Repository(data_dir)
    ok = compile_scene(repo, scene_of("act", {"s": "c1"}))
    assert ok.ok and len(warnings(ok.issues)) == 1 and ok.anima_prompt.split(", ")[4:6] == ["1girl", "solo"]

    broken = compile_scene(repo, scene_of("act", {"s": "nobody"}))
    assert not broken.ok and broken.moss_prompt == "" and broken.anima_prompt == "" and broken.negative_moss == ""
