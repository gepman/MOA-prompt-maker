"""差分のクリックで編集欄の該当箇所へ移動する（SPEC 10.1章）：ジャンプ先の位置の計算と、クリックできる HTML。"""
import json
import re

import pytest

from conftest import compile_sample
from moa.core.jsonpos import decoded_offsets, locate
from moa.core.quickdiff import field_rows, tag_spans
from moa.core.textutil import split_tags
from moa.ui import quickedit as Q
from moa.ui.app import build_ui
from moa.ui.diffhtml import rows_to_html
from moa.ui.style import JUMP_JS, TEXTBOX_ID

SCENE = "beach_chat__trio"


@pytest.fixture
def base(repo):
    r = compile_sample(repo, SCENE)
    assert r.ok
    return r


def dump(m):
    return json.dumps(m, indent=2, ensure_ascii=False)


def edited_moss(base, fn):
    m = json.loads(base.moss_prompt)
    fn(m)
    return dump(m)


def rows_with_pos(rows, kind=None):
    return [r for r in rows if r.pos is not None and (kind is None or r.kind == kind)]


# ---- JSON の位置 ----
def test_locate_finds_every_value():
    text = '{\n  "a": [1, {"b": "x\\"y"}],\n  "c": {}\n}'
    sp = locate(text)
    assert text[slice(*sp[("a", 0)])] == "1"
    assert text[slice(*sp[("a", 1, "b")])] == '"x\\"y"'
    assert text[slice(*sp[("c",)])] == "{}"
    assert text[slice(*sp[()])] == text


def test_decoded_offsets_count_escapes_as_one_character():
    text = '"a\\"b\\n\\u3042\\ud83d\\ude00z"'
    offs = decoded_offsets(text, (0, len(text)))
    assert len(offs) == len(json.loads(text)) + 1                 # a " b \n あ 😀 z ＋ 末尾
    assert text[offs[1]:offs[1] + 2] == '\\"' and text[offs[-2]] == "z" and offs[-1] == len(text) - 1


def test_locate_rejects_broken_json():
    with pytest.raises(ValueError):
        locate('{"a": ')


# ---- Anima ----
def test_added_tag_jumps_to_its_end(base):
    t = split_tags(base.anima_prompt)
    t.insert(t.index("standing") + 1, "sweat")
    new = ", ".join(t)
    (row,) = rows_with_pos(field_rows(base, "anima", new), "ins")
    assert new[:row.pos].endswith("standing, sweat") and new[row.pos:].startswith(", talking")


def test_deleted_tag_jumps_to_where_it_was(base):
    t = split_tags(base.anima_prompt)
    idx = [i for i, x in enumerate(t) if x == "huge breasts"][1]       # 2番目の huge breasts
    del t[idx]
    new = ", ".join(t)
    (row,) = rows_with_pos(field_rows(base, "anima", new), "del")
    assert new[:row.pos].endswith(t[idx - 1]) and new[row.pos:].startswith(f", {t[idx]}")


def test_tag_spans_ignore_surrounding_spaces_and_empty_tags():
    text = "  a ,, b  c ,d,"
    assert [text[a:b] for a, b in tag_spans(text)] == ["a", "b  c", "d"]
    assert len(tag_spans(text)) == len(split_tags(text))


def test_negative_tags_jump_in_the_negative_boxes_text(repo):
    r = compile_sample(repo, SCENE, apply_negative=True)
    new = r.negative_anima + ", extra"
    (row,) = rows_with_pos(field_rows(r, "nega", new), "ins")
    assert row.pos == len(new)


# ---- MOSS ----
def test_moss_word_change_jumps_to_the_end_of_the_new_word(base):
    new = edited_moss(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("mouth open,", "mouth closed,")))
    inline = next(r for r in field_rows(base, "moss", new) if r.kind == "inline")
    ins_pos = [p for (op, _), p in zip(inline.segs, inline.seg_pos) if op == "ins"]
    assert len(ins_pos) == 1 and new[:ins_pos[0]].endswith("with her mouth closed,")
    del_pos = [p for (op, _), p in zip(inline.segs, inline.seg_pos) if op == "del"]
    assert len(del_pos) == 1 and new[:del_pos[0]].endswith("with her mouth")   # 削除は、消えた箇所（前の語の直後）


def test_moss_position_counts_json_escapes(base):
    new = edited_moss(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("mouth open,", 'mouth "open" wide,')))
    inline = next(r for r in field_rows(base, "moss", new) if r.kind == "inline")
    p = next(p for (op, _), p in zip(inline.segs, inline.seg_pos) if op == "ins")
    assert new[:p].endswith('\\"open\\" wide,')                        # JSON のエスケープ（\"）を含む位置


def test_moss_word_deleted_jumps_to_where_it_was(base):
    new = edited_moss(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("with her mouth open, ", "with her ")))
    inline = next(r for r in field_rows(base, "moss", new) if r.kind == "inline")
    p = next(p for (op, _), p in zip(inline.segs, inline.seg_pos) if op == "del")
    assert new[:p].endswith("She laughs with her") and new[p:].startswith(" one hand raised")


def test_moss_bbox_and_items_jump_to_the_end_of_the_value(base):
    new = edited_moss(base, lambda m: m["items"][2].update(bbox=[-380, -190, 320, 200]))
    (pair,) = [r for r in field_rows(base, "moss", new) if r.kind == "pair"]
    assert new[:pair.pos].endswith("200\n      ]")
    obj = {"label": "object", "caption": "A small dog.", "bbox": [0, 0, 10, 10]}
    new = edited_moss(base, lambda m: m["items"].append(obj))
    (ins,) = rows_with_pos(field_rows(base, "moss", new), "ins")
    assert new[:ins.pos].endswith("]\n    }") and new[ins.pos:].startswith("\n  ]")
    new = edited_moss(base, lambda m: m["items"].pop(0))
    (dele,) = rows_with_pos(field_rows(base, "moss", new), "del")
    assert new[dele.pos:].lstrip().startswith('"label"') or new[:dele.pos].endswith("[")   # 消えた先頭の位置


def test_moss_meta_tag_jumps_inside_the_array(base):
    new = edited_moss(base, lambda m: m["summary"]["meta"].append("sweat"))
    (ins,) = rows_with_pos(field_rows(base, "moss", new), "ins")
    assert new[:ins.pos].endswith('"sweat') and new[ins.pos:].startswith('"')


def test_broken_json_jumps_to_the_end_of_the_line(base):
    new = base.moss_prompt.replace("Elesa", "Elesa!")[:-2]
    rows = rows_with_pos(field_rows(base, "moss", new), "ins")
    assert rows and all(new[r.pos] in "\n" or r.pos == len(new) for r in rows)
    r = next(r for r in rows if "Elesa! as she" in r.text)
    assert new[:r.pos].endswith(r.text) and new[r.pos:].startswith("\n")


# ---- クリックできる HTML ----
def test_changed_parts_and_row_buttons_carry_the_jump_target(base):
    t = split_tags(base.anima_prompt)
    t.insert(3, "sweat")
    new = ", ".join(t)
    html = rows_to_html(field_rows(base, "anima", new), "anima")
    pos = next(r.pos for r in field_rows(base, "anima", new) if r.kind == "ins")
    assert f'<ins class="moa-ins moa-jump" data-moa-field="anima" data-moa-pos="{pos}"' in html
    assert f'<button type="button" class="moa-go" data-moa-field="anima" data-moa-pos="{pos}"' in html


def test_inline_changes_each_carry_their_own_position(base):
    new = edited_moss(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("mouth open,", "mouth closed,").replace("talks.", "talks hard.")))
    html = rows_to_html(field_rows(base, "moss", new), "moss")
    assert len(set(re.findall(r'<ins class="moa-ins moa-jump" data-moa-field="moss" data-moa-pos="(\d+)"', html))) == 2


def test_without_a_field_nothing_is_clickable(base):
    html = rows_to_html(field_rows(base, "anima", base.anima_prompt + ", x"))
    assert "data-moa-pos" not in html and "moa-jump" not in html and "moa-go" not in html


def test_diff_html_from_the_editor_state_targets_the_right_box(base):
    qe = Q.set_edits(Q.refresh(Q.new_qe(), base, (1, "a", "b"), SCENE)[0],
                     {"anima": base.anima_prompt + ", x", "negm": base.negative_moss + ", y"})
    html = Q.field_diff_html(qe)
    assert 'data-moa-field="anima"' in html["anima"] and 'data-moa-field="negm"' in html["negm"]


def test_user_text_cannot_inject_attributes(base):
    html = rows_to_html(field_rows(base, "anima", base.anima_prompt + ', x" onclick="alert(1)'), "anima")
    assert 'onclick="alert' not in html and "&quot; onclick=&quot;alert(1)" in html


# ---- ページ側 ----
def test_page_has_the_jump_script_and_the_boxes_have_matching_ids(repo):
    import gradio as gr
    demo = build_ui(repo)
    assert "<script>" in demo.head and "__moaJump" in demo.head
    ids = {b.elem_id for b in demo.blocks.values() if isinstance(b, gr.Textbox) and b.elem_id}
    assert ids == {TEXTBOX_ID.format(f) for f in Q.FIELDS}
    assert "#moa-tb-' + el.getAttribute('data-moa-field')" in JUMP_JS
