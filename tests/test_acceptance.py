"""SPEC 11章 受け入れテスト。"""
import json
from dataclasses import replace

from conftest import EXAMPLES, edit, write
from moa.core import Repository, compile_scene

ELESA_CHAR_ANIMA = ("elesa (pokemon), pokemon, blonde hair, long hair, blunt bangs, "
                    "blue eyes, headphones, huge breasts")
ELESA_OUTFIT_ANIMA = "yellow shirt, sleeveless shirt, crop top, black shorts, thighhighs, high heels"
HEX_OUTFIT_ANIMA = "black sweater, cropped sweater, black skirt, flat shoes"


def _expected_negatives():
    text = (EXAMPLES / "beach_chat_negative.txt").read_text(encoding="utf-8")
    moss = text.split("[assembled MOSS negative]")[1].split("[assembled Anima negative]")[0].strip()
    anima = text.split("[assembled Anima negative]")[1].strip()
    return moss, anima


# 1. サンプルシーンのコンパイル結果が examples と一致する
def test_1_matches_examples(repo, trio):
    r = compile_scene(repo, trio)
    assert r.ok, r.issues
    assert r.moss_prompt == (EXAMPLES / "beach_chat_moss.json").read_text(encoding="utf-8").strip()
    assert r.anima_prompt == (EXAMPLES / "beach_chat_anima.txt").read_text(encoding="utf-8").strip()


# 2. centerを差し替えると、そのスロットだけが変わる
def test_2_swap_center_changes_only_that_slot(repo, data_dir, trio):
    write(data_dir / "characters" / "test" / "rika.yaml", """\
id: rika
name: リカ
defaultOutfit: newfit
moss: |
  Rika, an adult woman with short pink hair and extremely huge breasts.
anima: |
  rika (test), pink hair, short hair, huge breasts
""")
    write(data_dir / "outfits" / "test" / "newfit.yaml", """\
id: newfit
name: 新服
moss: |
  She wears a white dress.
anima: |
  white dress
""")
    repo.reload()
    base = compile_scene(repo, trio)
    swapped = compile_scene(repo, replace(trio, cast={**trio.cast, "center": "rika"}))
    assert swapped.ok, swapped.issues

    b, s = json.loads(base.moss_prompt), json.loads(swapped.moss_prompt)
    assert s["items"][0] == b["items"][0]
    assert s["items"][2] == b["items"][2]
    assert s["summary"] == b["summary"]
    assert s["items"][1]["bbox"] == b["items"][1]["bbox"]
    assert s["items"][1]["caption"] == (
        "Rika, an adult woman with short pink hair and extremely huge breasts. "
        "She wears a white dress. "
        "She laughs with her head tilted slightly back, both hands clasped in front of her chest.")

    expected = (base.anima_prompt
                .replace(ELESA_CHAR_ANIMA, "rika (test), pink hair, short hair, huge breasts")
                .replace(ELESA_OUTFIT_ANIMA, "white dress"))
    assert swapped.anima_prompt == expected


# 3. Situation側の服装指定は、キャラの defaultOutfit より優先される
def test_3_situation_outfit_beats_default(repo, data_dir, trio):
    edit(data_dir / "situations" / "beach" / "beach_chat.yaml",
         "mossPrompt: |", "outfits:\n  center: hex_summer\nmossPrompt: |")
    repo.reload()
    r = compile_scene(repo, trio)
    assert r.ok, r.issues
    center = next(s for s in r.slots if s.slot == "center")
    assert (center.outfit_id, center.outfit_source) == ("hex_summer", "situation")
    assert ELESA_OUTFIT_ANIMA not in r.anima_prompt
    assert r.anima_prompt.count(HEX_OUTFIT_ANIMA) == 2  # center と right

    # Scene側の指定はさらに優先される
    r2 = compile_scene(repo, replace(trio, outfits={"center": "skyla_pilot"}))
    center2 = next(s for s in r2.slots if s.slot == "center")
    assert (center2.outfit_id, center2.outfit_source) == ("skyla_pilot", "scene")


# 4. 服装が none のとき、壊れた文・空タグ・連続カンマが残らない
def test_4_none_outfit_leaves_no_debris(repo, trio):
    r = compile_scene(repo, replace(trio, outfits={"center": "none"}))
    assert r.ok, r.issues
    center = next(s for s in r.slots if s.slot == "center")
    assert (center.outfit_id, center.outfit_source) == (None, "none_scene")

    for item in json.loads(r.moss_prompt)["items"]:
        cap = item["caption"]
        assert "  " not in cap and cap == cap.strip()
    caption = json.loads(r.moss_prompt)["items"][1]["caption"]
    assert caption.endswith("antenna cables. She laughs with her head tilted slightly back, both hands clasped in front of her chest.")
    assert "yellow sleeveless" not in caption

    tags = r.anima_prompt
    assert ",," not in tags.replace(" ", "") and not tags.startswith(",") and not tags.endswith(",")
    assert all(t.strip() for t in tags.split(","))
    assert ELESA_OUTFIT_ANIMA not in tags


# 5. applyNegative の既定OFFでは送信ネガティブが空、ONでは定義どおりに合成される
def test_5_negative_apply(repo, trio):
    exp_moss, exp_anima = _expected_negatives()

    off = compile_scene(repo, trio)
    assert off.apply_negative is False
    assert off.negative_moss_payload == "" and off.negative_anima_payload == ""
    # OFFでも合成済みは見える
    assert off.negative_moss == exp_moss and off.negative_anima == exp_anima

    on = compile_scene(repo, replace(trio, apply_negative=True))
    assert on.negative_moss_payload == exp_moss
    assert on.negative_anima_payload == exp_anima


# 6. Animaの重複タグは書かれた通りに保持され、空タグ・連続カンマは残らない
def test_6_duplicate_tags_preserved(repo, trio):
    tags = compile_scene(repo, trio).anima_prompt.split(", ")
    assert tags.count("huge breasts") == 3
    assert tags.count("pokemon") == 3
    assert tags.count("crop top") == 2  # skyla_pilot と elesa_bw_summer
    assert "" not in tags
