"""SPEC v2 11章 受け入れテスト（15 の移行は test_migrate.py）。`spec_v2/samples/` を使う。"""
import json
from dataclasses import replace

import pytest

from conftest import (EXAMPLES, compile_sample, edit, errors, make_action, make_character, scene_of, warnings,
                      write)
from moa.core import CastEntry, Repository, compile_scene
from moa.core.repository import load_yaml
from moa.core.scene_io import save_scene

SCENES = ["beach_chat__trio", "beach_solo__elesa", "beach_wave_boy__elesa", "guilty_lineup__trio"]
ELESA_CHAR_ANIMA = ("elesa (pokemon), pokemon, blonde hair, long hair, blunt bangs, "
                    "blue eyes, headphones, huge breasts")


def read_example(scene_id: str):
    moss = (EXAMPLES / f"{scene_id}_moss.json").read_text(encoding="utf-8").strip()
    anima = (EXAMPLES / f"{scene_id}_anima.txt").read_text(encoding="utf-8").strip()
    neg = (EXAMPLES / f"{scene_id}_negative.txt").read_text(encoding="utf-8")
    apply = neg.splitlines()[0].startswith("applyNegative: true")
    neg_moss = neg.split("[assembled MOSS negative]")[1].split("[assembled Anima negative]")[0].strip()
    neg_anima = neg.split("[assembled Anima negative]")[1].strip()
    return moss, anima, apply, neg_moss, neg_anima


def sources(r):
    return {s.slot: (s.outfit_id, s.outfit_source) for s in r.slots}


# ---- 1. 4つのサンプル Scene の出力が examples と一致する ----
@pytest.mark.parametrize("scene_id", SCENES)
def test_1_samples_match_examples(repo, scene_id):
    moss, anima, apply, neg_moss, neg_anima = read_example(scene_id)
    scene = repo.scene(scene_id)
    r = compile_scene(repo, scene)

    assert r.ok and r.issues == [], r.issues          # サンプルは警告も出ない
    assert r.moss_prompt == moss
    assert r.anima_prompt == anima
    assert (r.negative_moss, r.negative_anima) == (neg_moss, neg_anima)   # 合成済みは適用OFFでも常に入る
    assert scene.apply_negative is apply
    if apply:
        assert (r.negative_moss_payload, r.negative_anima_payload) == (neg_moss, neg_anima)
    else:
        assert (r.negative_moss_payload, r.negative_anima_payload) == ("", "")


def test_1_highlights_beach_chat_trio(repo):
    r = compile_sample(repo, "beach_chat__trio")
    # 服装解決の3パターン：left・right は省略→defaultOutfit、center は Scene の none が Action の指定に勝つ
    assert sources(r) == {"left": ("skyla_pilot", "character"), "center": (None, "none_scene"),
                          "right": ("hex_summer", "character")}
    items = json.loads(r.moss_prompt)["items"]
    assert items[0]["caption"].startswith("The sea and the shoreline")   # Background の mossItems が先頭
    assert len(items) == 4


def test_1_highlights_beach_solo_elesa(repo):
    r = compile_sample(repo, "beach_solo__elesa")
    assert sources(r) == {"solo": ("elesa_sling", "action")}   # Action の指定が defaultOutfit に勝つ
    assert r.rating == "sensitive"                              # Background safe × Action sensitive
    assert r.people_tags == "1girl, solo"
    assert "She has sparse thin blonde pubic hair." in r.moss_prompt   # {{solo.bodyHair}} の展開
    assert "sparse pubic hair, blonde pubic hair" in r.anima_prompt


def test_1_highlights_guilty_lineup(repo):
    r = compile_sample(repo, "guilty_lineup__trio")
    assert sources(r) == {"left": ("guilty_suit_pink", "action"), "center": ("guilty_suit_yellow", "action"),
                          "right": ("guilty_suit_blue", "action")}
    moss = json.loads(r.moss_prompt)
    assert moss["summary"]["meta"] == ["anime illustration", "safe", "idol", "unit lineup", "costume swap"]
    assert len(moss["items"]) == 3                              # 白背景は mossItems なし


def test_1_highlights_beach_wave_boy(repo):
    r = compile_sample(repo, "beach_wave_boy__elesa")
    assert r.people_tags == "1girl, 1boy" and "solo" not in r.anima_prompt.split(", ")   # extras で solo なし
    assert sources(r) == {"girl": ("hex_summer", "scene")}     # Scene の上書きが defaultOutfit に勝つ
    assert r.apply_negative is True and r.negative_anima_payload == r.negative_anima


# ---- 4. center を差し替えると、そのスロットの分だけが変わる ----
def test_4_swap_center_changes_only_that_slot(data_dir):
    make_character(data_dir, "rika", moss="Rika, an adult woman with short pink hair and extremely huge breasts.",
                   anima="rika (test), pink hair, short hair, huge breasts")
    repo = Repository(data_dir)
    base = compile_sample(repo, "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    swapped = compile_scene(repo, replace(scene, cast={**scene.cast, "center": CastEntry("rika", "none")}))
    assert swapped.ok, swapped.issues

    b, s = json.loads(base.moss_prompt), json.loads(swapped.moss_prompt)
    assert s["summary"] == b["summary"]
    for i in (0, 1, 3):                                         # Background、left、right の item は同じ
        assert s["items"][i] == b["items"][i]
    assert s["items"][2]["bbox"] == b["items"][2]["bbox"]
    assert s["items"][2]["caption"] == ("Rika, an adult woman with short pink hair and extremely huge breasts. "
                                        "She laughs with her head tilted slightly back, both hands clasped in front of her chest.")

    assert swapped.anima_prompt == base.anima_prompt.replace(ELESA_CHAR_ANIMA,
                                                             "rika (test), pink hair, short hair, huge breasts")
    assert "short hair" in base.negative_moss.split(", ") and "short hair" not in swapped.negative_moss.split(", ")


# ---- 5. 服装の三値 ----
def test_5_outfit_delegation_order_and_none(data_dir):
    cast = {"left": CastEntry("skyla_bw"), "center": CastEntry("elesa_bw"), "right": CastEntry("hex_maniac_xy")}
    repo = Repository(data_dir)
    trio = repo.scene("beach_chat__trio")

    # 省略 → Action → Character の順に委譲
    r = compile_scene(repo, replace(trio, cast=cast))
    assert sources(r) == {"left": ("skyla_pilot", "character"), "center": ("elesa_bw_summer", "action"),
                          "right": ("hex_summer", "character")}

    # Scene の id 指定が最優先
    r = compile_scene(repo, replace(trio, cast={**cast, "center": CastEntry("elesa_bw", "skyla_pilot")}))
    assert sources(r)["center"] == ("skyla_pilot", "scene")

    # none：服装テキストなし。壊れた文・空タグ・連続カンマが残らない
    all_none = {s: CastEntry(e.character, "none") for s, e in cast.items()}
    r = compile_scene(repo, replace(trio, cast=all_none))
    assert r.ok and all(v == (None, "none_scene") for v in sources(r).values())
    for it in json.loads(r.moss_prompt)["items"]:
        assert "  " not in it["caption"] and it["caption"] == it["caption"].strip()
    tags = r.anima_prompt.split(", ")
    assert "" not in tags and ", ," not in r.anima_prompt
    assert "blue gloves" not in tags and "black sweater" not in tags and "yellow shirt" not in tags


def test_5_none_and_unset_at_each_layer(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "center: elesa_bw_summer", "center: none")
    edit(data_dir / "characters" / "skyla_bw.yaml", "defaultOutfit: skyla_pilot", "defaultOutfit: none")
    edit(data_dir / "characters" / "hex_maniac_xy.yaml", "defaultOutfit: hex_summer\n", "")
    repo = Repository(data_dir)
    cast = {"left": CastEntry("skyla_bw"), "center": CastEntry("elesa_bw"), "right": CastEntry("hex_maniac_xy")}
    r = compile_scene(repo, replace(repo.scene("beach_chat__trio"), cast=cast))
    assert r.ok
    assert sources(r) == {"left": (None, "none_character"), "center": (None, "none_action"),
                          "right": (None, "unset")}


@pytest.mark.parametrize("path,old,new", [
    ("scenes/beach_chat__trio.yaml", "    outfit: none\n", "    outfit: null\n"),
    ("actions/beach_chat.yaml", "center: elesa_bw_summer", "center: null"),
    ("characters/elesa_bw.yaml", "defaultOutfit: elesa_bw_summer", "defaultOutfit: null"),
])
def test_5_null_outfit_is_a_schema_error(data_dir, path, old, new):
    edit(data_dir / path, old, new)
    repo = Repository(data_dir)
    assert any("スキーマ違反" in i.message and path.split("/")[1] in str(i.path) for i in errors(repo.issues))
    kind = {"scenes": "scene", "actions": "action", "characters": "character"}[path.split("/")[0]]
    assert repo.entry(kind, path.split("/")[1].removesuffix(".yaml")) is None   # 索引から外れる


# ---- 6. 人数タグと solo ----
@pytest.mark.parametrize("females,males,extras,expected", [
    (1, 0, None, "1girl, solo"),
    (0, 1, None, "1boy, solo"),
    (2, 1, None, "2girls, 1boy"),
    (5, 0, None, "5girls"),
    (6, 0, None, "6+girls"),
    (7, 0, None, "6+girls"),
    (0, 6, None, "6+boys"),
    (3, 2, None, "3girls, 2boys"),
    (1, 0, {"others": 3}, "1girl"),                 # others は人数タグを作らず、solo を打ち消す
    (1, 0, {"male": 1}, "1girl, 1boy"),
    (1, 0, {"female": 1}, "2girls"),
    (0, 1, {"others": 1}, "1boy"),
])
def test_6_people_tags(data_dir, females, males, extras, expected):
    cast = {}
    for i in range(females):
        make_character(data_dir, f"f{i}")
        cast[f"s{len(cast)}"] = f"f{i}"
    for i in range(males):
        make_character(data_dir, f"m{i}", sex="male")
        cast[f"s{len(cast)}"] = f"m{i}"
    make_action(data_dir, "crowd", list(cast), extras=extras, anima=", ".join("{{%s}}" % s for s in cast))
    repo = Repository(data_dir)

    r = compile_scene(repo, scene_of("crowd", cast))
    assert r.ok, r.issues
    assert r.people_tags == expected
    assert r.anima_prompt.startswith(f"masterpiece, best quality, score_7, safe, {expected}, ")
    if extras and "others" in extras:
        assert "others" not in r.anima_prompt and "3" not in r.people_tags


def test_6_extras_do_not_change_moss(data_dir):
    make_character(data_dir, "f0")
    make_action(data_dir, "a1", ["s"], anima="{{s}}")
    make_action(data_dir, "a2", ["s"], anima="{{s}}", extras={"male": 2})
    repo = Repository(data_dir)
    one = compile_scene(repo, scene_of("a1", {"s": "f0"}))
    two = compile_scene(repo, scene_of("a2", {"s": "f0"}))
    assert one.moss_prompt == two.moss_prompt            # extras は Anima の人数タグと solo だけに効く
    assert one.people_tags == "1girl, solo" and two.people_tags == "1girl, 2boys"


# ---- 7. テンプレートの人数タグは除去して警告。キャラブロック内は除去しない ----
def test_7_template_count_tags_are_removed_with_one_warning_each(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "  {{left}}, {{center}}, {{right}}, {{left.outfit}}",
         "  3girls, Solo, 1BOY, 6+girls, {{left}}, {{center}}, {{right}}, {{left.outfit}}")
    edit(data_dir / "backgrounds" / "beach.yaml", "  beach, ocean", "  2girls, beach, ocean")
    repo = Repository(data_dir)
    r = compile_sample(repo, "beach_chat__trio")

    moss, anima, *_ = read_example("beach_chat__trio")
    assert r.ok and r.anima_prompt == anima              # 除去後は同じ。生成された 3girls が1回だけ残る
    msgs = [i.message for i in warnings(r.issues)]
    assert len(msgs) == 5
    for tag, where in (("3girls", "Action 'beach_chat'"), ("Solo", "Action 'beach_chat'"), ("1BOY", "Action 'beach_chat'"),
                       ("6+girls", "Action 'beach_chat'"), ("2girls", "Background 'beach'")):
        assert any(f"'{tag}'" in m and where in m for m in msgs), (tag, msgs)


def test_7_tags_inside_character_blocks_are_kept(data_dir):
    edit(data_dir / "characters" / "elesa_bw.yaml", "  elesa (pokemon), pokemon,", "  1girl, solo, elesa (pokemon), pokemon,")
    repo = Repository(data_dir)
    base = read_example("beach_chat__trio")[1]
    r = compile_sample(repo, "beach_chat__trio")
    assert r.anima_prompt == base.replace("elesa (pokemon)", "1girl, solo, elesa (pokemon)", 1)   # キャラのタグは除去されない
    assert warnings(r.issues) == []


def test_7_non_girl_boy_count_expressions_are_not_removed(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "standing, talking", "3children, 2people, standing, talking")
    r = compile_sample(Repository(data_dir), "beach_chat__trio")
    tags = r.anima_prompt.split(", ")
    assert "3children" in tags and "2people" in tags and warnings(r.issues) == []


# ---- 8. rating ----
@pytest.mark.parametrize("bg,action,expected", [
    ("safe", "explicit", "explicit"),
    ("sensitive", "safe", "sensitive"),
    ("explicit", "sensitive", "explicit"),
    ("safe", "safe", "safe"),
])
def test_8_effective_rating_is_the_stricter(data_dir, bg, action, expected):
    edit(data_dir / "backgrounds" / "beach.yaml", "rating: safe", f"rating: {bg}")
    edit(data_dir / "actions" / "beach_chat.yaml", "rating: safe", f"rating: {action}")
    r = compile_sample(Repository(data_dir), "beach_chat__trio")
    assert r.rating == expected
    assert json.loads(r.moss_prompt)["summary"]["meta"][:2] == ["anime illustration", expected]
    assert r.anima_prompt.startswith(f"masterpiece, best quality, score_7, {expected}, 3girls, ")


def test_8_rating_defaults_to_safe_and_meta_dedupes(data_dir):
    edit(data_dir / "backgrounds" / "beach.yaml", "rating: safe\n", "")
    edit(data_dir / "actions" / "beach_chat.yaml", "metaTags: [group portrait]", "metaTags: [beach scene, safe, group portrait]")
    r = compile_sample(Repository(data_dir), "beach_chat__trio")
    assert r.rating == "safe"
    assert json.loads(r.moss_prompt)["summary"]["meta"] == ["anime illustration", "safe", "beach scene", "group portrait"]


# ---- 9. bbox ----
def test_9_overflow_warnings_report_side_and_amount(data_dir):
    edit(data_dir / "backgrounds" / "beach.yaml", "[-512, -768, -80, 768]", "[-512, -800, -80, 768]")
    edit(data_dir / "actions" / "beach_chat.yaml", "[-380, -640, 320, -260]", "[-380, -640, 600, -260]")
    repo = Repository(data_dir)
    r = compile_sample(repo, "beach_chat__trio")
    assert r.ok                                           # 警告どまり
    msgs = [i.message for i in warnings(r.issues)]
    assert any("Background 'beach'" in m and "1番目" in m and "label: object" in m and "左 32px" in m for m in msgs)
    assert any("Action 'beach_chat'" in m and "1番目" in m and "下 88px" in m for m in msgs)
    assert all("完全にはみ出し" not in m for m in msgs)
    assert any("左 32px" in i.message for i in warnings(repo.issues))   # 読み込み時にも出る（Background は自身の canvas）


def test_9_action_items_are_checked_against_the_scenes_background_canvas(data_dir):
    write(data_dir / "backgrounds" / "small.yaml",
          "id: small\nname: 小さい背景\ncanvas: { w: 1000, h: 600 }\nmossCaption: \"\"\nanima: \"\"\n")
    repo = Repository(data_dir)
    r = compile_sample(repo, "beach_chat__trio", background="small")
    assert r.ok and r.canvas == {"w": 1000, "h": 600}
    msgs = " ".join(i.message for i in warnings(r.issues))
    assert "左 140px" in msgs and "上 80px" in msgs and "下 20px" in msgs      # [-380,-640,320,-260] vs ±500 × ±300
    assert not warnings(compile_sample(repo, "beach_chat__trio").issues)       # 元の Background ではなし


def test_9_completely_outside_is_distinguished(data_dir):
    edit(data_dir / "actions" / "beach_chat.yaml", "[-380, -640, 320, -260]", "[600, -100, 700, 100]")
    r = compile_sample(Repository(data_dir), "beach_chat__trio")
    assert r.ok and any("完全にはみ出し" in i.message and "下" in i.message for i in warnings(r.issues))


@pytest.mark.parametrize("path,old,new,who", [
    ("actions/beach_chat.yaml", "[-380, -640, 320, -260]", "[320, -640, -380, -260]", "Action 'beach_chat'"),
    ("actions/beach_chat.yaml", "[-380, -190, 320, 190]", "[-380, 190, 320, -190]", "Action 'beach_chat'"),
    ("backgrounds/beach.yaml", "[-512, -768, -80, 768]", "[-512, 768, -80, -768]", "Background 'beach'"),
])
def test_9_inverted_bbox_is_an_error(data_dir, path, old, new, who):
    edit(data_dir / path, old, new)
    repo = Repository(data_dir)
    assert any("大小が逆転" in i.message and who in i.message for i in errors(repo.issues))
    r = compile_sample(repo, "beach_chat__trio")
    assert not r.ok and r.moss_prompt == ""               # エラーがあればコンパイル結果を出さない


# ---- 10. Background にプレイスホルダーがあるとエラー ----
@pytest.mark.parametrize("old,new,label", [
    ("A sunny beach in the daytime,", "A sunny beach of {{left}} in the daytime,", "mossCaption"),
    ("The sea and the shoreline,", "The sea {{left.outfit}} and the shoreline,", "mossItems[1].caption"),
    ("  beach, ocean", "  beach, {{center.bodyHair}}, ocean", "anima"),
    ("  beach, ocean", "  beach, {{nope.foo}}, ocean", "anima"),
])
def test_10_placeholders_in_background_are_errors(data_dir, old, new, label):
    edit(data_dir / "backgrounds" / "beach.yaml", old, new)
    repo = Repository(data_dir)
    assert any("Background 'beach'" in i.message and label in i.message for i in errors(repo.issues)), repo.issues
    r = compile_sample(repo, "beach_chat__trio")
    assert not r.ok and r.moss_prompt == ""


# ---- 11. ネガティブ：applyNegative と層の順 ----
def test_11_negative_layers_are_base_character_outfit_background_action(data_dir):
    edit(data_dir / "characters" / "skyla_bw.yaml", "anima: |", "negativeMoss: m_char\nanima: |")
    edit(data_dir / "outfits" / "skyla_pilot.yaml", "anima: |", "negativeMoss: m_outfit\nanima: |")
    edit(data_dir / "backgrounds" / "beach.yaml", "  indoor, interior, room, city, street, night, snow, rain", "  m_bg")
    edit(data_dir / "actions" / "beach_chat.yaml",
         "  duplicate, split screen, text, watermark, blurry, low quality, worst quality", "  m_action")
    r = compile_sample(Repository(data_dir), "beach_chat__trio")
    tags = r.negative_moss.split(", ")
    order = [tags.index(t) for t in ("mosaic", "m_char", "short hair", "m_outfit", "m_bg", "m_action")]
    assert order == sorted(order) and len(set(order)) == 6   # base → キャスト（left, center）→ 服装 → Background → Action
    assert r.negative_moss_payload == ""                      # 既定OFFでは送信ペイロードは空


def test_11_apply_negative_true_sends_the_assembled_negative(repo):
    r = compile_sample(repo, "beach_chat__trio", apply_negative=True)
    assert r.negative_moss_payload == r.negative_moss and r.negative_anima_payload == r.negative_anima
    assert r.negative_moss_payload != "" and r.negative_anima_payload != ""


# ---- 12. Animaの重複タグは保持される ----
def test_12_duplicate_tags_are_kept(repo):
    tags = compile_sample(repo, "beach_chat__trio").anima_prompt.split(", ")
    assert tags.count("huge breasts") == 3 and tags.count("pokemon") == 3
    assert "" not in tags


# ---- 13. 体毛 ----
def test_13_body_hair_is_empty_for_a_character_without_it(data_dir):
    repo = Repository(data_dir)
    swapped = scene_of("beach_solo", {"solo": "skyla_bw"}, id="beach_solo__skyla")    # skyla は体毛なし
    r = compile_scene(repo, swapped)
    assert r.ok, r.issues

    skyla = repo.character("skyla_bw").moss.strip()
    sling = repo.outfit("elesa_sling").moss.strip()
    caption = json.loads(r.moss_prompt)["items"][1]["caption"]
    assert caption == (f"{skyla} {sling} She lies on her back on a beach towel with her eyes closed and a relaxed smile, "
                       "her arms resting loosely at her sides.")                  # 体毛の文ごと消える
    tags = r.anima_prompt.split(", ")
    assert "" not in tags and "pubic" not in r.anima_prompt
    assert tags[tags.index("black swimsuit") + 1] == "lying"                       # 空のタグが詰まる


# ---- 14. 保存：コメントが残る。compiled に background / action / canvas / rating が入る ----
def test_14_save_keeps_comments_and_writes_the_snapshot(repo, data_dir):
    path = repo.path_of("scene", "beach_chat__trio")
    scene = repo.scene("beach_chat__trio")
    r = compile_scene(repo, scene)
    save_scene(repo, scene, r)

    text = path.read_text(encoding="utf-8")
    for comment in ("# outfit 省略 → Action 指定なし → defaultOutfit (skyla_pilot) が効く",
                    "# Scene の none が Action の elesa_bw_summer 指定に勝つ",
                    "# outfit 省略 → Action 指定なし → defaultOutfit (hex_summer) が効く"):
        assert comment in text
    data = load_yaml(path)
    assert list(data)[:5] == ["id", "name", "background", "action", "cast"]        # キー順が保たれる
    assert data["cast"]["center"] == {"character": "elesa_bw", "outfit": "none"}
    c = data["compiled"]
    assert (c["background"], c["action"], c["canvas"], c["rating"]) == ("beach", "beach_chat", {"w": 1536, "h": 1024}, "safe")
    assert c["mossPrompt"].strip() == r.moss_prompt and c["animaPrompt"].strip() == r.anima_prompt
    assert c["negativeApplied"] is False
    assert not errors(Repository(data_dir).issues)                                 # 書いた結果も v2 スキーマに通る
