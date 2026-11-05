"""EDITOR_SPEC 13章の受け入れテスト（1〜13）。UI 部品ではなく moa/editor/logic の関数を試す。14 は test_ui_logic.py。"""
import copy
import os

import pytest

from conftest import errors, warnings
from moa.core.repository import load_yaml
from moa.editor.logic import cards, forms, session
from moa.editor.logic.forms import MAX_ITEMS, RECOMMENDED_ITEMS
from moa.editor.logic.preview import preview
from moa.editor.logic.save import (CONFIRM_COW, CONFIRM_USED_ACTION, ERRORS, EXTERNAL, SAVED, save_draft,
                                   save_folder)
from moa.editor.logic.tree import listing, make_folder
from moa.editor.logic.validation import validate_draft


def open_edit(repo, kind, eid):
    st = session.open_entry(repo, session.new_state(), repo.entry(kind, eid))
    return st, copy.deepcopy(st["loaded"])


def start_new(repo, section, **fields):
    st = session.new_state()
    st = session.perform(repo, st, {"action": "section", "section": section})
    st = session.start_new(repo, st)
    return st, copy.deepcopy(st["loaded"]) | fields


def bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def msgs(issues):
    return " / ".join(i.message for i in issues)


# 1. コメント保持
def test_01_comments_and_order_preserved(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    before = path.read_text(encoding="utf-8")
    st, v = open_edit(repo, "character", "elesa_bw")
    out = save_draft(repo, st, v | {"name": "カミツレ改"})
    assert out.status == SAVED
    after = path.read_text(encoding="utf-8")
    assert "# sex は省略 = female（デフォルト）" in after and "# sensitive 用途向けのデフォルト" in after
    assert after == before.replace("name: カミツレ（BW版）", "name: カミツレ改")
    assert list(load_yaml(path)) == list(load_yaml_keys(before))


def load_yaml_keys(text):
    from ruamel.yaml import YAML
    return YAML(typ="safe").load(text).keys()


# 2. ID の検証
def test_02_id_validation(repo):
    base = {"name": "x", "moss": "m", "anima": "a"}
    for eid in ("elesa_bw", "Elesa", ""):
        st, v = start_new(repo, "characters", id=eid, **base)
        issues = validate_draft(repo, "character", v, new=True)
        assert any(i.field == "id" for i in errors(issues)), eid
    st, v = start_new(repo, "outfits", id="none", **base)
    assert any(i.field == "id" and "none" in i.message for i in errors(validate_draft(repo, "outfit", v, new=True)))
    st, v = start_new(repo, "characters", id="elesa_bw_copy", **base)
    assert not errors(validate_draft(repo, "character", v, new=True))
    # ルートが違えば同じ id でよい
    st, v = start_new(repo, "outfits", id="elesa_bw", **base)
    assert not errors(validate_draft(repo, "outfit", v, new=True))


# 3. 複製
def test_03_copy(repo, data_dir):
    st, v = open_edit(repo, "character", "skyla_bw")
    st = session.start_copy(repo, st, v)
    assert st["loaded"]["id"] == "skyla_bw_copy" and st["save_dir"] == "characters"
    out = save_draft(repo, st, copy.deepcopy(st["loaded"]))
    assert out.status == SAVED
    new = data_dir / "characters" / "skyla_bw_copy.yaml"
    assert new.exists() and out.state["mode"] == "edit" and out.state["id"] == "skyla_bw_copy"
    a, b = load_yaml(data_dir / "characters" / "skyla_bw.yaml"), load_yaml(new)
    assert b.pop("id") == "skyla_bw_copy" and a.pop("id") == "skyla_bw" and a == b


def test_03b_copy_goes_to_source_folder(repo, data_dir):
    sub = data_dir / "characters" / "pokemon"
    sub.mkdir()
    (data_dir / "characters" / "skyla_bw.yaml").rename(sub / "skyla_bw.yaml")
    repo.reload()
    st, v = open_edit(repo, "character", "skyla_bw")
    st = session.start_copy(repo, st, v)
    assert st["save_dir"] == "characters/pokemon"
    assert save_draft(repo, st, st["loaded"]).path == sub / "skyla_bw_copy.yaml"


# 4. Background のコピーオンライト
def test_04_background_copy_on_write(repo, data_dir):
    beach = data_dir / "backgrounds" / "beach.yaml"
    before = beach.read_bytes()
    st, v = open_edit(repo, "background", "beach")
    v["mossCaption"] = "A rainy beach."
    out = save_draft(repo, st, v)
    assert out.status == CONFIRM_COW and len(out.scenes) == 3 and out.default_new_id == "beach_copy"
    assert {sid for _, sid in out.scenes} == {"beach_chat__trio", "beach_solo__elesa", "beach_wave_boy__elesa"}
    assert beach.read_bytes() == before
    # 新 ID の検証
    assert save_draft(repo, st, v, confirmed=True, cow_id="beach").status == "cow_id_error"
    assert save_draft(repo, st, v, confirmed=True, cow_id="Bad Id").status == "cow_id_error"
    out = save_draft(repo, st, v, confirmed=True, cow_id="beach_copy")
    assert out.status == SAVED and beach.read_bytes() == before
    assert load_yaml(data_dir / "backgrounds" / "beach_copy.yaml")["mossCaption"].strip() == "A rainy beach."
    assert out.state["id"] == "beach_copy"
    assert repo.scene("beach_chat__trio").background == "beach"   # 既存 Scene は元の Background のまま


def test_04b_unused_background_overwritten_in_place(repo, data_dir):
    st, v = start_new(repo, "backgrounds", id="my_bg", name="新しい背景")
    out = save_draft(repo, st, v)
    assert out.status == SAVED
    path = data_dir / "backgrounds" / "my_bg.yaml"
    st = out.state
    v = copy.deepcopy(st["loaded"]) | {"mossCaption": "Edited."}
    out = save_draft(repo, st, v)
    assert out.status == SAVED and out.path == path
    assert load_yaml(path)["mossCaption"].strip() == "Edited."
    assert not (data_dir / "backgrounds" / "my_bg_copy.yaml").exists()


# 5. 使用中 Action の上書き
def test_05_used_action_overwritten(repo, data_dir):
    path = data_dir / "actions" / "beach_chat.yaml"
    st, v = open_edit(repo, "action", "beach_chat")
    v["mossCaption"] += " They smile."
    out = save_draft(repo, st, v)
    assert out.status == CONFIRM_USED_ACTION and [sid for _, sid in out.scenes] == ["beach_chat__trio"]
    assert "They smile" not in path.read_text(encoding="utf-8")
    out = save_draft(repo, st, v, confirmed=True)
    assert out.status == SAVED and out.path == path and "They smile." in load_yaml(path)["mossCaption"]
    assert not (data_dir / "actions" / "beach_chat_copy.yaml").exists()


# 6. スロットの保護
def test_06_slot_protection(repo, data_dir):
    path = data_dir / "actions" / "beach_chat.yaml"
    before = path.read_bytes()
    st, v = open_edit(repo, "action", "beach_chat")
    for slots in (["center", "right"], ["l", "center", "right"]):
        out = save_draft(repo, st, v | {"slots": slots}, confirmed=True)
        assert out.status == ERRORS and "beach_chat__trio" in msgs(out.issues), slots
    assert path.read_bytes() == before

    plus = v | {"slots": v["slots"] + ["extra"]}
    out = save_draft(repo, st, plus)
    assert out.status == CONFIRM_USED_ACTION
    assert any(i.level == "warning" and "beach_chat__trio" in i.message and i.field == "slots" for i in out.issues)
    out = save_draft(repo, st, plus, confirmed=True)
    assert out.status == SAVED and "extra" in load_yaml(path)["slots"]
    assert any("beach_chat__trio" in i.message for i in warnings(out.issues))


def test_06b_unreferenced_new_action_can_rename_main(repo, data_dir):
    st, v = start_new(repo, "actions", id="fresh_action", name="新規")
    out = save_draft(repo, st, v)
    assert out.status == SAVED
    st = out.state
    out = save_draft(repo, st, copy.deepcopy(st["loaded"]) | {"slots": ["solo"]})
    assert out.status == SAVED and load_yaml(out.path)["slots"] == ["solo"]


# 7. 保存時の検証
def test_07_validation(repo):
    st, v = open_edit(repo, "background", "beach")
    issues = validate_draft(repo, "background", v | {"mossCaption": "A beach with {{left}}."}, new=False)
    assert any("プレイスホルダー" in i.message for i in errors(issues))

    st, v = open_edit(repo, "action", "beach_solo")
    issues = validate_draft(repo, "action", v | {"anima": v["anima"] + ", 3girls"}, new=False, original_id="beach_solo")
    assert not errors(issues) and any("3girls" in i.message for i in warnings(issues))

    def with_bbox(v, bbox):
        items = copy.deepcopy(v["mossItems"])
        items[0]["bbox"] = bbox
        return v | {"mossItems": items}

    stb, vb = open_edit(repo, "background", "beach")
    over = validate_draft(repo, "background", with_bbox(vb, [-600, -768, 0, 768]), new=False)
    assert not errors(over) and any("はみ出" in i.message for i in warnings(over))
    inv = validate_draft(repo, "background", with_bbox(vb, [0, 100, -10, 50]), new=False)
    assert any("逆転" in i.message for i in errors(inv))
    over = validate_draft(repo, "action", with_bbox(v, [-600, -768, 0, 768]), new=False, original_id="beach_solo")
    assert not errors(over) and any("はみ出" in i.message for i in warnings(over))
    inv = validate_draft(repo, "action", with_bbox(v, [0, 100, -10, 50]), new=False, original_id="beach_solo")
    assert any("逆転" in i.message for i in errors(inv))


def test_07b_anima_count_tag_saves(repo, data_dir):
    st, v = open_edit(repo, "action", "beach_solo")
    out = save_draft(repo, st, v | {"anima": v["anima"] + ", 3girls"}, confirmed=True)
    assert out.status == SAVED and any("3girls" in i.message for i in out.issues)


def test_07c_empty_name_is_error(repo):
    st, v = open_edit(repo, "outfit", "hex_summer")
    assert any(i.field == "name" for i in errors(validate_draft(repo, "outfit", v | {"name": " "}, new=False)))


# 8. 三値コントロール
def test_08_three_state_outfit(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    st, v = open_edit(repo, "character", "elesa_bw")
    assert save_draft(repo, st, v | {"defaultOutfit": forms.outfit_value("unset", None)}).status == SAVED
    assert "defaultOutfit" not in load_yaml(path)
    st = session.open_entry(repo, st, repo.entry("character", "elesa_bw"))
    assert save_draft(repo, st, v | {"defaultOutfit": forms.outfit_value("none", None)}).status == SAVED
    assert load_yaml(path)["defaultOutfit"] == "none"
    st = session.open_entry(repo, st, repo.entry("character", "elesa_bw"))
    assert save_draft(repo, st, v | {"defaultOutfit": forms.outfit_value("outfit", "hex_summer")}).status == SAVED
    assert load_yaml(path)["defaultOutfit"] == "hex_summer"
    assert "null" not in path.read_text(encoding="utf-8")


def test_08b_action_outfits(repo, data_dir):
    st, v = open_edit(repo, "action", "guilty_lineup")
    outfits = dict(v["outfits"])
    slot = next(iter(outfits))
    v2 = v | {"outfits": {**outfits, slot: forms.outfit_value("unset", None)}}   # 未指定にするとキーが消える
    out = save_draft(repo, st, v2, confirmed=True)
    assert out.status == SAVED and slot not in load_yaml(out.path).get("outfits", {})
    assert "null" not in out.path.read_text(encoding="utf-8")


# 9. extras
def test_09_extras(repo, data_dir):
    path = data_dir / "actions" / "beach_wave_boy.yaml"
    st, v = open_edit(repo, "action", "beach_wave_boy")
    out = save_draft(repo, st, v | {"extras": {"female": 0, "male": 1, "others": 0}}, confirmed=True)
    assert out.status == SAVED and load_yaml(path)["extras"] == {"male": 1}
    assert "extras: {male: 1}" in path.read_text(encoding="utf-8")
    st = out.state
    out = save_draft(repo, st, copy.deepcopy(st["loaded"]) | {"extras": {"female": 0, "male": 0, "others": 0}},
                     confirmed=True)
    assert out.status == SAVED and "extras" not in load_yaml(path)


# 10. 外部変更の検知
def test_10_external_change(repo, data_dir):
    path = data_dir / "characters" / "skyla_bw.yaml"
    st, v = open_edit(repo, "character", "skyla_bw")
    path.write_text(path.read_text(encoding="utf-8") + "# 外部で追記\n", encoding="utf-8")
    bump(path)
    out = save_draft(repo, st, v | {"name": "編集後"})
    assert out.status == EXTERNAL and "外部で変更" in out.message
    assert "編集後" not in path.read_text(encoding="utf-8")
    out = save_draft(repo, st, v | {"name": "編集後"}, force=True)
    assert out.status == SAVED and load_yaml(path)["name"] == "編集後"
    # 再読み込み（編集を破棄）
    path.write_text(path.read_text(encoding="utf-8") + "# もう一度\n", encoding="utf-8")
    bump(path)
    st2 = session.reload_current(repo, out.state)
    assert st2["mode"] == "edit" and save_draft(repo, st2, st2["loaded"]).status == SAVED


# 11. フォルダ
def test_11_folders(repo, data_dir):
    st = session.new_state()
    for bad in ("CON", "con.txt", "_x", ".x", "a?b", "末尾. ", ""):
        msg, made = make_folder(repo, st, bad)
        assert msg.startswith("⚠") and made == "", bad
    msg, made = make_folder(repo, st, "ポケモン")
    assert made == "characters/ポケモン" and (data_dir / "characters" / "ポケモン").is_dir()
    assert not (data_dir / "characters" / "ポケモン" / "_folder.yaml").exists()
    assert make_folder(repo, st, "ポケモン")[0].startswith("⚠")

    st = session.perform(repo, st, {"action": "dir", "rel": made})
    assert st["mode"] == "folder"
    out = save_folder(repo, st, {"name": "ポケモン", "description": "", "order": "nothing.yaml"})
    assert out.status == SAVED and any("nothing.yaml" in i.message for i in warnings(out.issues))
    assert load_yaml(data_dir / "characters" / "ポケモン" / "_folder.yaml")["name"] == "ポケモン"
    assert any("ポケモン" in label for label, _ in listing(repo, session.new_state())[0])


# 12. プレビュー
def test_12_preview_character_draft(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    before = path.read_bytes()
    st, v = open_edit(repo, "character", "elesa_bw")
    res = preview(repo, "character", v | {"moss": "DRAFT MOSS TEXT."}, original_id="elesa_bw",
                  choices={"action": "beach_chat", "slot": "center"})
    assert res.ok and "DRAFT MOSS TEXT." in res.moss
    notes = "\n".join(res.notes)
    assert "補完：left = skyla_bw（Scene beach_chat__trio から）" in notes
    assert "補完：right = hex_maniac_xy（Scene beach_chat__trio から）" in notes
    assert path.read_bytes() == before and "DRAFT MOSS" not in repo.character("elesa_bw").moss


def test_12b_preview_outfit_draft_beats_action(repo):
    st, v = open_edit(repo, "outfit", "hex_summer")
    res = preview(repo, "outfit", v | {"moss": "She wears DRAFT OUTFIT."}, original_id="hex_summer",
                  choices={"action": "beach_solo", "slot": "solo"})
    assert res.ok
    assert "She wears DRAFT OUTFIT." in res.moss
    assert any("通常の解決順では Action 指定（elesa_sling）に負けます" in n for n in res.notes)


def test_12c_preview_unsaved_new_action(repo):
    st, v = start_new(repo, "actions", id="not_saved_yet", name="未保存", mossCaption="They stand.", anima="standing")
    res = preview(repo, "action", v)
    assert res.ok and "They stand." in res.moss and "standing" in res.anima
    assert repo.action("not_saved_yet") is None


def test_12d_preview_background_and_action_defaults(repo):
    st, v = open_edit(repo, "background", "white_background")
    res = preview(repo, "background", v | {"mossCaption": "DRAFT WHITE."}, original_id="white_background")
    assert res.ok and res.chosen["action"] == "guilty_lineup" and "DRAFT WHITE." in res.moss
    st, v = open_edit(repo, "action", "beach_solo")
    res = preview(repo, "action", v, original_id="beach_solo")
    assert res.ok and res.chosen["background"] == "beach"


# 13. カード
def test_13_cards(repo, data_dir):
    canvas = {"w": 1536, "h": 1024}
    items = []
    for _ in range(RECOMMENDED_ITEMS):             # おすすめの8件までは普通に増やせる
        assert cards.can_add(items)
        items = cards.add_item(items, canvas)
    assert cards.can_add(items) and cards.add_hint(items) == ""      # 8件を超えても追加できる（実際の上限は32件）
    while len(items) < MAX_ITEMS:
        items = cards.add_item(items, canvas)
    assert len(items) == 32 and not cards.can_add(items) and cards.add_hint(items) == "最大32件です"
    assert cards.add_item(items, canvas) == items

    assert cards.set_full([{"label": "object", "caption": "", "bbox": [0, 0, 1, 1]}], 0, canvas)[0]["bbox"] == [-512, -768, 512, 768]

    st, v = open_edit(repo, "action", "beach_chat")
    captions = [i["caption"] for i in v["mossItems"]]
    assert len(captions) >= 2
    moved = cards.move_down(v["mossItems"], 0)
    assert [i["caption"] for i in moved][:2] == [captions[1], captions[0]]
    assert [i["caption"] for i in cards.move_up(moved, 1)] == captions
    out = save_draft(repo, st, v | {"mossItems": moved}, confirmed=True)
    assert out.status == SAVED
    assert [i["caption"].strip() for i in load_yaml(out.path)["mossItems"]][:2] == [c.strip() for c in (captions[1], captions[0])]


# 未保存の変更で移動を止める（3章）
def test_unsaved_change_blocks_navigation(repo):
    st, v = open_edit(repo, "character", "skyla_bw")
    st2, done = session.request(repo, st, v | {"name": "変更"}, {"action": "section", "section": "outfits"})
    assert not done and st2["pending"] and st2["section"] == "characters"
    assert session.resolve_pending(repo, st2, discard=False)["pending"] is None
    moved = session.resolve_pending(repo, st2, discard=True)
    assert moved["section"] == "outfits" and moved["mode"] is None
    st3, done = session.request(repo, st, v, {"action": "section", "section": "outfits"})
    assert done and st3["section"] == "outfits"


def test_listing_shows_scene_count(repo):
    st = session.perform(repo, session.new_state(), {"action": "section", "section": "backgrounds"})
    labels = [l for l, _ in listing(repo, st)[0]]
    assert any("海辺（beach.yaml）・Scene 3件" == l.split(" ", 1)[1] for l in labels)
    assert not any("白背景" in l and "Scene 0" in l for l in labels)
