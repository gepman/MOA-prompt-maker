"""エディタの差分をクリックすると、編集欄の該当箇所へカーソルとスクロールを動かす（EDITOR_SPEC 14.6章。ビルダーと同じ JS）。"""
import re

import gradio as gr
import pytest

from moa.core.diffcore import to_text
from moa.editor.logic import diffs as D
from moa.editor.logic import forms
from moa.editor.ui.app import build_editor
from moa.ui.style import JUMP_JS, TEXTBOX_ID
from test_editor_build import Driver


@pytest.fixture
def old(repo):
    return forms.values_of(repo.character("skyla_bw"))


def jumps(html):
    """(欄の名前, 位置) の並び（出てくる順）。"""
    return [(f, int(p)) for f, p in re.findall(r'data-moa-field="([^"]+)" data-moa-pos="(\d+)"', html)]


# ---- 長い欄 ----
def test_added_tag_jumps_to_its_end(old):
    new = {**old, "anima": old["anima"] + ", sweat"}
    (row,) = [r for r in D.text_rows("anima", old["anima"], new["anima"], "anima") if r.kind == "ins"]
    assert row.target == "anima" and new["anima"][:row.pos].endswith(", sweat") and row.pos == len(new["anima"])


def test_deleted_tag_jumps_to_where_it_was(old):
    tags = old["anima"].split(", ")
    idx = 3                                                                   # 4番目のタグを消す
    new_text = ", ".join(tags[:idx] + tags[idx + 1:])
    (row,) = [r for r in D.text_rows("anima", old["anima"], new_text, "anima") if r.kind == "del"]
    assert new_text[:row.pos].endswith(tags[idx - 1]) and new_text[row.pos:].startswith(f", {tags[idx + 1]}")


def test_positions_count_the_leading_whitespace_of_the_field(old):
    raw = "  \n" + old["anima"] + ", sweat"            # 欄の文字列は前後の空白を除く前の値。差分はその位置で返す
    html = D.views("character", {**old, "anima": raw}, old)["fields"]["anima"]
    (field, pos), *_ = jumps(html)
    assert field == "anima" and raw[:pos].endswith(", sweat") and pos == len(raw)
    caption_raw = "   " + old["moss"].replace("auburn", "black")
    html = D.views("character", {**old, "moss": caption_raw}, old)["fields"]["moss"]
    assert any(caption_raw[:p].endswith("black") for _, p in jumps(html))     # 追加した語の最後尾


def test_word_change_jumps_to_the_end_of_the_new_word(old):
    new = old["moss"].replace("auburn", "jet black")
    (row,) = D.text_rows("moss", old["moss"], new, "moss")
    ins = [p for (op, _), p in zip(row.segs, row.seg_pos) if op == "ins"]
    assert len(ins) == 1 and new[:ins[0]].endswith("jet black")
    dele = [p for (op, _), p in zip(row.segs, row.seg_pos) if op == "del"]
    assert len(dele) == 1 and new[:dele[0]].endswith("with")                 # 削除は、消えた箇所（前の語の直後）


def test_without_a_target_nothing_is_clickable(old):
    html = D.views("character", {**old, "anima": old["anima"] + ", x"}, old)["fields"]["anima"]
    assert "moa-jump" in html
    from moa.ui.diffhtml import rows_to_html
    assert "data-moa-pos" not in rows_to_html(D.text_rows("anima", "a", "a, b"))


# ---- カードの caption ----
def test_card_caption_jumps_inside_its_own_box(repo):
    old = forms.values_of(repo.action("beach_chat"))["mossItems"]
    raw = [dict(c) for c in old]
    raw[1]["caption"] = "  " + old[1]["caption"].replace("head tilted", "head tipped") + " Sweat."
    views = D.card_views(old, [{**c, "caption": c["caption"].strip()} for c in raw], raw)
    assert views[0] == [] and views[2] == []
    inline = next(r for r in views[1] if r.kind == "inline")
    assert inline.target == "c_caption1"
    ins = [p for (op, _), p in zip(inline.segs, inline.seg_pos) if op == "ins"]
    assert raw[1]["caption"][:ins[-1]].endswith("Sweat.")                    # 先頭の空白を数え直した位置
    assert D.card_views(old, raw)[1][1].target is None                       # 画面の値がなければ移動しない


def test_card_label_and_bbox_rows_are_not_clickable(repo):
    old = forms.values_of(repo.action("beach_chat"))["mossItems"]
    new = [{**old[0], "label": "text", "bbox": [0, 0, 1, 1]}, *old[1:]]
    rows = D.card_views(old, new, new)[0]
    assert rows and all(r.pos is None for r in rows)


# ---- 変更点の一覧（行ごとに別の欄へ移動）----
def test_the_change_list_jumps_to_each_field(repo):
    old = forms.values_of(repo.action("beach_chat"))
    new = {**old, "name": "海辺で雑談  ", "tags": "日常, 海", "slots": "left, center, right, extra",
           "anima": old["anima"] + ", sweat", "mossCaption": old["mossCaption"] + " Calm.",
           "mossItems": [{**c, "caption": c["caption"] + " X."} if i == 2 else c for i, c in enumerate(old["mossItems"])]}
    rows = D.change_rows("action", new, old)
    targets = {r.target for r in rows if r.pos is not None}
    assert targets == {"name", "tags", "slots", "anima", "mossCaption", "c_caption2"}
    name_row = next(r for r in rows if r.target == "name")
    assert name_row.pos == len("海辺で雑談")                                  # 末尾の空白の手前
    tags_row = next(r for r in rows if r.target == "tags")
    assert tags_row.kind == "ins" and new["tags"][:tags_row.pos].endswith("海")
    html = D.changes_html("action", new, old)
    assert {f for f, _ in jumps(html)} == targets
    assert "moa-go" in html and "移動" in html


def test_short_items_other_than_text_boxes_are_not_clickable(repo):
    old = forms.values_of(repo.background("beach"))
    html = D.changes_html("background", {**old, "rating": "sensitive", "canvas": {"w": 1024, "h": 1024}}, old)
    assert "rating" in html and "canvas" in html and "data-moa-pos" not in html


def test_lists_given_as_values_are_not_clickable(repo):
    old = forms.values_of(repo.action("beach_chat"))
    html = D.changes_html("action", {**old, "tags": ["日常", "海"]}, old)
    assert "海" in html and "data-moa-pos" not in html


def test_text_output_is_not_affected_by_jump_targets(old):
    plain = [to_text(r) for r in D.text_rows("anima", "a, b", "a, c")]
    jump = [to_text(r) for r in D.text_rows("anima", "a, b", "a, c", "anima")]
    assert plain == jump


# ---- 画面 ----
def test_screen_has_the_script_and_matching_ids(repo):
    demo = build_editor(repo)
    assert "<script>" in demo.head and "__moaJump" in demo.head
    ids = {b.elem_id for b in demo.blocks.values() if isinstance(b, gr.Textbox) and (b.elem_id or "").startswith("moa-tb-")}
    wanted = {"name", "tags", "group", "metaTags", "slots", *D.LONG_KEYS, *(f"c_caption{i}" for i in range(8))}
    assert ids == {TEXTBOX_ID.format(n) for n in wanted}
    assert "input[type=text]" in JUMP_JS and "#moa-tb-' + el.getAttribute('data-moa-field')" in JUMP_JS


def test_screen_diff_html_carries_the_jump_targets(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/skyla_bw.yaml")
    d["anima"] = d["anima"] + ", sweat"
    d["name"] = d["name"] + "（改）"
    d.call("diff_anima")
    field_html = d.update_of("df_anima")["value"]
    assert jumps(field_html) and {f for f, _ in jumps(field_html)} == {"anima"}
    assert jumps(field_html)[0][1] == len(d["anima"])
    panel = d.update_of("chg_html")["value"]
    assert {f for f, _ in jumps(panel)} == {"anima", "name"}


def test_screen_card_diff_targets_the_card_caption(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:actions/beach_chat.yaml")
    d["c_caption1"] = d["c_caption1"] + " Sweat."
    d.call("diff_c_caption1")
    html = d.update_of("c_df1")["value"]
    assert {f for f, _ in jumps(html)} == {"c_caption1"} and jumps(html)[0][1] == len(d["c_caption1"])
