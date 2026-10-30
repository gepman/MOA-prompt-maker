"""体毛（bodyHair）：キャラの bodyHairMoss / bodyHairAnima を {{slot.bodyHair}} で差し込む。"""
import json
import re
import shutil
from dataclasses import replace

import pytest

from conftest import EXAMPLES, SAMPLES, edit, errors, warnings
from moa.core import Repository, compile_scene

BH_MOSS = "She has sparse thin brown pubic hair."          # 完全な文（服装の moss と同じ）
BH_ANIMA = "sparse pubic hair, brown pubic hair"
NO_HAIR_CAST = {"left": "skyla_bw", "center": "hex_maniac_xy", "right": "skyla_bw"}  # 体毛を持つキャラがいない


def _sit(data_dir):
    return data_dir / "situations" / "beach" / "beach_chat.yaml"


def build(data_dir, moss=BH_MOSS, anima=BH_ANIMA, placeholders=True) -> Repository:
    """elesa_bw にだけ体毛を持たせ、beach_chat の各スロットに {{slot.bodyHair}} を置く。"""
    extra = ""
    if moss is not None:
        extra += "bodyHairMoss: " + json.dumps(moss, ensure_ascii=False) + "\n"
    if anima is not None:
        extra += "bodyHairAnima: " + json.dumps(anima, ensure_ascii=False) + "\n"
    elesa = data_dir / "characters" / "pokemon" / "elesa_bw.yaml"
    elesa.write_text(elesa.read_text(encoding="utf-8") + extra, encoding="utf-8", newline="\n")

    if placeholders:
        sit = _sit(data_dir)
        for slot, tail in (("left", "She laughs with her mouth"), ("center", "She laughs with her head"),
                           ("right", "She giggles")):
            edit(sit, "{{" + slot + ".outfit}} " + tail,
                 "{{" + slot + ".outfit}} {{" + slot + ".bodyHair}} " + tail)
        edit(sit, "{{right.outfit}}, beach",
             "{{right.outfit}}, {{left.bodyHair}}, {{center.bodyHair}}, {{right.bodyHair}}, beach")
    return Repository(data_dir)


def compile_trio(repo, **changes):
    return compile_scene(repo, replace(repo.scene("beach_chat__trio"), **changes))


@pytest.fixture
def bh(data_dir):
    return build(data_dir)


@pytest.fixture
def orig(tmp_path):
    """プレースホルダーを置く前の、元のサンプル。"""
    shutil.copytree(SAMPLES / "data", tmp_path / "orig")
    return Repository(tmp_path / "orig")


# ---- 量＋色の解決 ----
def test_resolves_amount_and_color_in_the_right_field(bh):
    r = compile_trio(bh)
    assert r.ok and r.issues == [], r.issues

    # MOSS：服装の文 → 体毛の文 → 続きの文（center = elesa）
    caption = json.loads(r.moss_prompt)["items"][1]["caption"]
    assert f"high heels. {BH_MOSS} She laughs with her head tilted" in caption

    # Anima：体毛のタグが、位置どおり（center の分）に1回だけ入る
    tags = r.anima_prompt.split(", ")
    i = tags.index("flat shoes")
    assert tags[i + 1: i + 4] == ["sparse pubic hair", "brown pubic hair", "beach"]
    assert tags.count("sparse pubic hair") == 1

    # MOSS は bodyHairMoss、Anima は bodyHairAnima だけが使われる
    assert BH_ANIMA not in r.moss_prompt
    assert "thin" not in r.anima_prompt and "She has" not in r.anima_prompt


def test_body_hair_moss_resolves_as_a_complete_sentence(bh):
    """体毛あり（elesa = center）：服装の文と同じ自己完結した文として、前後の文の間に入る。"""
    caption = json.loads(compile_trio(bh).moss_prompt)["items"][1]["caption"]
    elesa = bh.character("elesa_bw").moss.strip()
    outfit = bh.outfit("elesa_bw_summer").moss.strip()
    assert caption == (f"{elesa} {outfit} {BH_MOSS} "
                       "She laughs with her head tilted slightly back, both hands clasped in front of her chest.")
    sentences = re.split(r"(?<=\.) ", caption)
    assert BH_MOSS in sentences                                   # 体毛の文が、1文としてそのまま入っている
    assert all(s[0].isupper() and s.endswith(".") for s in sentences)


def test_without_body_hair_the_whole_sentence_disappears(bh):
    """体毛なし（skyla = left）：文ごと消え、空白・句読点の残りもない。"""
    caption = json.loads(compile_trio(bh).moss_prompt)["items"][0]["caption"]
    expected = json.loads((EXAMPLES / "beach_chat_moss.json").read_text(encoding="utf-8"))["items"][0]["caption"]
    assert caption == expected                                    # プレースホルダーを置く前と完全一致
    assert "pubic" not in caption and "She has" not in caption
    assert "  " not in caption and " ." not in caption and not re.search(r"\.\s*\.", caption)


def test_outfit_and_body_hair_resolve_independently(bh):
    r = compile_trio(bh, outfits={"center": "none"})          # 服装を外しても体毛は残る
    caption = json.loads(r.moss_prompt)["items"][1]["caption"]
    assert f"antenna cables. {BH_MOSS} She laughs" in caption and "yellow sleeveless" not in caption
    assert "sparse pubic hair" in r.anima_prompt and "yellow shirt" not in r.anima_prompt


# ---- 未設定時の空文字化 ----
def test_unset_resolves_to_empty_with_no_error_or_leftover_spaces(bh):
    r = compile_trio(bh)
    expected = json.loads((EXAMPLES / "beach_chat_moss.json").read_text(encoding="utf-8"))["items"]
    items = json.loads(r.moss_prompt)["items"]
    assert items[0] == expected[0] and items[2] == expected[2]   # skyla / hex は体毛なし → 元の出力と完全一致


def test_nobody_has_body_hair_gives_exactly_the_original_output(bh, orig):
    with_placeholders = compile_trio(bh, cast=NO_HAIR_CAST)
    original = compile_trio(orig, cast=NO_HAIR_CAST)
    assert with_placeholders.ok and with_placeholders.issues == []
    assert with_placeholders.moss_prompt == original.moss_prompt
    assert with_placeholders.anima_prompt == original.anima_prompt


# ---- Anima の空タグ除去 ----
def test_anima_leaves_no_empty_tags_or_stray_commas(bh):
    tags_text = compile_trio(bh).anima_prompt
    tags = tags_text.split(", ")
    assert "" not in tags and ",," not in tags_text.replace(" ", "")
    assert not tags_text.startswith(",") and not tags_text.endswith(",")


def test_anima_closes_the_gap_when_nobody_has_body_hair(bh):
    tags = compile_trio(bh, cast=NO_HAIR_CAST).anima_prompt.split(", ")
    assert tags[tags.index("beach") - 1] == "boots"               # right の服装の最後の次に beach：空の3つ分が詰まる
    assert "" not in tags


@pytest.mark.parametrize("anima", [" \n ", "", " , ,, "])
def test_blank_body_hair_anima_behaves_like_unset(data_dir, anima):
    repo = build(data_dir, anima=anima)
    tags = compile_trio(repo).anima_prompt.split(", ")
    assert "" not in tags and tags[tags.index("flat shoes") + 1] == "beach"


# ---- 検証 ----
@pytest.mark.parametrize("target,old,new,label", [
    ("moss", "{{left.outfit}} She laughs", "{{left.outfit}} {{ghost.bodyHair}} She laughs", "mossPrompt"),
    ("anima", "{{right.outfit}}, beach", "{{right.outfit}}, {{ghost.bodyHair}}, beach", "animaPrompt"),
])
def test_undeclared_slot_is_an_error(data_dir, target, old, new, label):
    edit(_sit(data_dir), old, new)
    repo = Repository(data_dir)
    r = compile_trio(repo)
    assert not r.ok
    assert any("ghost" in i.message and label in i.message for i in errors(r.issues)), r.issues
    assert any("ghost" in i.message for i in errors(repo.issues))   # 読み込み時の検証にも出る


@pytest.mark.parametrize("bad", ["{{left.bodyhair}}", "{{left.hair}}", "{{left.bodyHair.x}}", "{{.bodyHair}}"])
def test_unknown_forms_are_still_malformed(data_dir, bad):
    edit(_sit(data_dir), "{{left.outfit}} She laughs", "{{left.outfit}} " + bad + " She laughs")
    r = compile_trio(Repository(data_dir))
    assert not r.ok and any("不正なプレイスホルダー" in i.message for i in errors(r.issues)), r.issues


def test_inner_spaces_and_repeated_use_are_allowed(data_dir):
    sit = _sit(data_dir)
    build(data_dir, placeholders=False)
    edit(sit, "{{center.outfit}} She laughs", "{{center.outfit}} {{ center.bodyHair }} She laughs")
    edit(sit, "in a friendly conversation.", "in a friendly conversation. {{center.bodyHair}}")
    moss = json.loads(compile_trio(Repository(data_dir)).moss_prompt)
    assert moss["summary"]["caption"].endswith("friendly conversation. " + BH_MOSS)
    assert f"high heels. {BH_MOSS} She laughs" in moss["items"][1]["caption"]


def test_a_slot_used_only_through_body_hair_counts_as_used(data_dir):
    edit(_sit(data_dir), "slots: [left, center, right]", "slots: [left, center, right, extra]")
    assert any("extra" in i.message for i in warnings(Repository(data_dir).issues))   # 対照：未使用なら警告

    edit(_sit(data_dir), "{{right.outfit}}, beach", "{{right.outfit}}, {{extra.bodyHair}}, beach")
    assert not any("extra" in i.message for i in warnings(Repository(data_dir).issues))


# ---- スキーマ ----
def test_character_fields_are_loaded(data_dir):
    repo = build(data_dir)
    elesa = repo.character("elesa_bw")
    assert (elesa.body_hair_moss, elesa.body_hair_anima) == (BH_MOSS, BH_ANIMA)
    skyla = repo.character("skyla_bw")
    assert (skyla.body_hair_moss, skyla.body_hair_anima) == ("", "")


def test_only_one_side_is_enough(data_dir):
    repo = build(data_dir, anima=None)                              # bodyHairMoss だけ
    assert not errors(repo.issues) and repo.character("elesa_bw").body_hair_anima == ""
    r = compile_trio(repo)
    assert BH_MOSS in r.moss_prompt and "pubic hair" not in r.anima_prompt


@pytest.mark.parametrize("field", ["bodyHairMoss", "bodyHairAnima"])
def test_non_string_is_a_schema_error(data_dir, field):
    elesa = data_dir / "characters" / "pokemon" / "elesa_bw.yaml"
    elesa.write_text(elesa.read_text(encoding="utf-8") + f"{field}: [a, b]\n", encoding="utf-8", newline="\n")
    repo = Repository(data_dir)
    assert repo.character("elesa_bw") is None
    assert any(field in i.message for i in errors(repo.issues))
