"""8章（ファイル配置）と 9.1章（スキーマ検証）。"""
import pytest

from conftest import SAMPLES, edit, errors, make_action, make_character, warnings, write
from moa.core import Repository
from moa.core.tree import breadcrumb, list_folder


def test_sample_data_loads_cleanly(repo):
    assert repo.issues == []
    assert {k: len(repo.ids(k)) for k in ("character", "outfit", "background", "action", "scene")} == \
        {"character": 6, "outfit": 7, "background": 2, "action": 4, "scene": 4}
    assert repo.character("elesa_bw").sex == "female"             # sex 省略 = female
    assert repo.action("beach_wave_boy").extras == {"male": 1}
    assert repo.background("white_background").moss_items == []   # mossItems 省略 = 空
    assert repo.background("beach").rating == "safe"


def test_scene_model_keeps_the_nested_cast(repo):
    scene = repo.scene("beach_chat__trio")
    assert scene.background == "beach" and scene.action == "beach_chat"
    assert {s: (e.character, e.outfit) for s, e in scene.cast.items()} == {
        "left": ("skyla_bw", None), "center": ("elesa_bw", "none"), "right": ("hex_maniac_xy", None)}
    assert scene.gen == {"moss": {"steps": 50, "cfg": 3.0}, "anima": {"steps": 14, "cfg": 1.8, "denoise": 0.45}}


def test_ids_are_unique_per_root_not_across_roots(data_dir):
    # Action と Scene と Background が同じ id でもよい（ルートが違う）
    write(data_dir / "scenes" / "beach_chat.yaml",
          "id: beach_chat\nbackground: beach\naction: beach_chat\ncast:\n  left: {character: skyla_bw}\n"
          "  center: {character: elesa_bw}\n  right: {character: hex_maniac_xy}\n")
    write(data_dir / "backgrounds" / "beach_chat.yaml",
          "id: beach_chat\nname: x\ncanvas: {w: 10, h: 10}\nmossCaption: ''\nanima: ''\n")
    repo = Repository(data_dir)
    assert errors(repo.issues) == []
    assert repo.scene("beach_chat") and repo.action("beach_chat") and repo.background("beach_chat")


def test_duplicate_id_within_a_root_is_an_error_first_one_wins(data_dir):
    write(data_dir / "characters" / "zzz" / "skyla_bw.yaml", "id: skyla_bw\nname: dup\nmoss: m\nanima: a\n")
    repo = Repository(data_dir)
    assert any("重複" in i.message and "skyla_bw" in i.message for i in errors(repo.issues))
    assert repo.character("skyla_bw").name == "フウロ（BW版）"     # 先に見つかった方が残る


def test_id_must_match_the_filename(data_dir):
    write(data_dir / "outfits" / "wrong_name.yaml", "id: other_id\nname: x\nmoss: m\nanima: a\n")
    repo = Repository(data_dir)
    assert any("一致しません" in i.message and "wrong_name.yaml" in i.message for i in errors(repo.issues))
    assert repo.outfit("other_id") is None and repo.outfit("wrong_name") is None


def test_folders_starting_with_underscore_or_dot_are_not_scanned(data_dir):
    make_character(data_dir, "visible")
    for folder in ("_draft", ".hidden", "ok/_nested"):
        write(data_dir / "characters" / folder / "ghost.yaml", "id: ghost\nname: g\nmoss: m\nanima: a\n")
        write(data_dir / "characters" / folder / "bad.yaml", "this: [is not valid")
    write(data_dir / "characters" / "ok" / "fine.yaml", "id: fine\nname: f\nmoss: m\nanima: a\n")
    repo = Repository(data_dir)
    assert repo.character("visible") and repo.character("fine") and repo.character("ghost") is None
    assert errors(repo.issues) == []                                  # 読み込まれないので、壊れたファイルもエラーにならない


def test_folder_meta_is_read_but_a_file_named_underscore_x_is_still_scanned(data_dir):
    write(data_dir / "actions" / "sub" / "_folder.yaml", "name: サブ\n")
    write(data_dir / "actions" / "sub" / "_x.yaml", "id: _x\nname: x\nslots: [a]\nmossCaption: ''\nmossItems: []\nanima: '{{a}}'\n")
    repo = Repository(data_dir)
    assert repo.folder_meta[data_dir.resolve() / "actions" / "sub"].name == "サブ"
    assert repo.action("_x") is not None and errors(repo.issues) == []   # 除外されるのはフォルダだけ。`_` 始まりの id も有効


def test_arbitrary_depth_and_non_yaml_files(data_dir):
    write(data_dir / "scenes" / "a" / "b" / "c" / "deep.yaml",
          "id: deep\nbackground: beach\naction: beach_solo\ncast:\n  solo: {character: elesa_bw}\n")
    write(data_dir / "scenes" / "a" / "notes.txt", "ignored")
    repo = Repository(data_dir)
    assert repo.scene("deep") and repo.rel(repo.path_of("scene", "deep")) == "scenes/a/b/c/deep.yaml"
    assert repo.root_dir("scene") == data_dir.resolve() / "scenes"


def test_unparsable_yaml_is_reported_and_loading_continues(data_dir):
    write(data_dir / "outfits" / "broken.yaml", "id: broken\nname: [unclosed\n")
    repo = Repository(data_dir)
    assert any("YAMLを読み込めません" in i.message and "broken.yaml" in str(i.path) for i in errors(repo.issues))
    assert repo.outfit("hex_summer") is not None


@pytest.mark.parametrize("path,old,new,key", [
    # v1.x の形式は v2 ではスキーマエラー（14章のツールで移行する）
    ("scenes/beach_chat__trio.yaml", "background: beach\naction: beach_chat\n", "situation: beach_chat\n", "situation"),
    ("scenes/beach_chat__trio.yaml", "applyNegative: false\n", "applyNegative: false\noutfits: {left: skyla_pilot}\n", "outfits"),
    ("scenes/beach_chat__trio.yaml", "applyNegative: false\n", "applyNegative: false\ndenoise: 0.5\n", "denoise"),
    ("scenes/beach_chat__trio.yaml", "    character: skyla_bw\n", "    character: skyla_bw\n    extra: 1\n", "extra"),
    ("scenes/beach_chat__trio.yaml", "  left:\n    character: skyla_bw", "  left: skyla_bw", "left"),   # 旧形式の cast（id 直書き）
    # その他のスキーマ違反
    ("characters/elesa_bw.yaml", "id: elesa_bw", "id: elesa_bw\nsex: other", "sex"),
    ("backgrounds/beach.yaml", "rating: safe", "rating: nsfw", "rating"),
    ("backgrounds/beach.yaml", "tags: [海辺]", "tags: [海辺]\nsurprise: 1", "surprise"),
    ("actions/beach_chat.yaml", "slots: [left, center, right]", "slots: []", "slots"),
    ("actions/beach_wave_boy.yaml", "extras: { male: 1 }", "extras: { male: -1 }", "male"),
    ("actions/beach_wave_boy.yaml", "extras: { male: 1 }", "extras: { robots: 1 }", "robots"),
    ("actions/beach_chat.yaml", "bbox: [-380, -640, 320, -260]", "bbox: [-380, -640, 320]", "bbox"),
    ("actions/beach_chat.yaml", "bbox: [-380, -640, 320, -260]", "bbox: [-380, -640, 320, -260.5]", "bbox"),
])
def test_schema_violations_are_errors_and_remove_the_file_from_the_index(data_dir, path, old, new, key):
    edit(data_dir / path, old, new)
    repo = Repository(data_dir)
    kind = {"scenes": "scene", "characters": "character", "backgrounds": "background", "actions": "action"}[path.split("/")[0]]
    eid = path.split("/")[1].removesuffix(".yaml")
    assert repo.entry(kind, eid) is None
    assert any("スキーマ違反" in i.message and key in i.message and eid in str(i.path) for i in errors(repo.issues)), repo.issues


def test_a_v1_situation_file_in_actions_is_rejected(data_dir):
    v1 = (SAMPLES.parents[1] / "spec" / "samples" / "data" / "situations" / "beach" / "beach_chat.yaml")
    write(data_dir / "actions" / "beach_chat_v1.yaml", v1.read_text(encoding="utf-8").replace("id: beach_chat", "id: beach_chat_v1"))
    repo = Repository(data_dir)
    assert repo.action("beach_chat_v1") is None
    assert any("beach_chat_v1.yaml" in str(i.path) for i in errors(repo.issues))


def test_outfit_id_none_is_reserved(data_dir):
    write(data_dir / "outfits" / "none.yaml", "id: none\nname: x\nmoss: m\nanima: a\n")
    repo = Repository(data_dir)
    assert repo.outfit("none") is None and any("none.yaml" in str(i.path) for i in errors(repo.issues))


def test_character_without_required_fields(data_dir):
    write(data_dir / "characters" / "bad.yaml", "id: bad\nname: x\nmoss: m\n")        # anima 欠落
    repo = Repository(data_dir)
    assert repo.character("bad") is None and any("anima" in i.message for i in errors(repo.issues))


# ---- ツリー（共有ブラウザ用の一覧）----
def test_tree_order_labels_and_natural_sort(repo, data_dir):
    base = data_dir / "scenes"
    for name in ("10_b", "2_a", "1_z"):
        write(base / name / "_folder.yaml", f"name: 表示{name}\n")
    repo.reload()
    assert [it.label for it in list_folder(repo, base)][:3] == ["📁 表示1_z", "📁 表示2_a", "📁 表示10_b"]

    write(base / "_folder.yaml", "order:\n  - 10_b\n  - beach_chat__trio.yaml\n")
    repo.reload()
    assert [it.path.name for it in list_folder(repo, base)][:2] == ["10_b", "beach_chat__trio.yaml"]


def test_tree_hides_underscore_and_dot_folders_and_shows_names(repo, data_dir):
    write(data_dir / "actions" / "_draft" / "x.yaml", "id: x\n")
    write(data_dir / "actions" / ".hidden" / "y.yaml", "id: y\n")
    items = list_folder(repo, data_dir / "actions")
    assert all(it.kind == "file" for it in items)                      # サンプルはフォルダ平置き
    assert items[0].label == "📄 海辺で談笑（beach_chat.yaml）"
    assert [it.path.name for it in items] == ["beach_chat.yaml", "beach_solo.yaml", "beach_wave_boy.yaml", "guilty_lineup.yaml"]
    assert list_folder(repo, data_dir / "scenes")[0].label.startswith("🎬 ")


def test_breadcrumb_uses_folder_names(repo, data_dir):
    write(data_dir / "scenes" / "pokemon" / "_folder.yaml", "name: ポケモン\n")
    repo.reload()
    crumbs = breadcrumb(repo, data_dir / "scenes", data_dir / "scenes" / "pokemon")
    assert [c[0] for c in crumbs] == ["scenes", "ポケモン"]
