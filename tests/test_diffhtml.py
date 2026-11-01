"""色付き差分表示とプロンプト欄の文字サイズ（SPEC 10.1章）。"""
import json
import re

import pytest

from conftest import compile_sample, write
from moa.core import CastEntry
from moa.core.appconfig import config_prompt_font_size
from moa.core.quickdiff import build_diff, field_rows, to_text
from moa.core.textutil import split_tags
from moa.ui import quickedit as Q
from moa.ui.app import build_ui
from moa.ui.diffhtml import rows_to_html
from moa.ui.style import prompt_css

SCENE = "beach_chat__trio"
KEY = (1, "beach_chat", "beach")


@pytest.fixture
def base(repo):
    r = compile_sample(repo, SCENE)
    assert r.ok
    return r


def moss_with(base, fn):
    m = json.loads(base.moss_prompt)
    fn(m)
    return json.dumps(m, indent=2, ensure_ascii=False)


def html_of(base, fld, new):
    return rows_to_html(field_rows(base, fld, new))


def tags(html, cls):
    """<del class="moa-del">x</del> / <ins class="moa-ins">x</ins> の中身（出てくる順）。"""
    tag = "del" if cls == "del" else "ins"
    return re.findall(rf'<{tag} class="moa-{cls}">(.*?)</{tag}>', html)


# ---- 追加と削除が、正しい位置・種類で入る ----
def test_anima_delete_and_insert_are_rendered_in_order(base):
    t = split_tags(base.anima_prompt)
    t.remove("outdoors")
    t.insert(t.index("standing") + 1, "sweat")
    html = html_of(base, "anima", ", ".join(t))
    assert tags(html, "del") == ["outdoors"] and tags(html, "ins") == ["sweat"]
    assert html.index("moa-ins") < html.index("moa-del")                   # standing の直後（前方）に追加、末尾近くを削除
    assert '<div class="moa-head">[Anima]</div>' in html
    assert html.index("<ins") < html.index("<del")


def test_repeated_anima_tag_deletes_only_the_chosen_position(base):
    t = split_tags(base.anima_prompt)
    idx = [i for i, x in enumerate(t) if x == "huge breasts"][1]
    del t[idx]
    html = html_of(base, "anima", ", ".join(t))
    assert tags(html, "del") == ["huge breasts"] and tags(html, "ins") == []
    assert "Character elesa_bw の anima" in html


def test_moss_word_diff_marks_the_words(base):
    moss = moss_with(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("mouth open,", "mouth closed,")))
    html = html_of(base, "moss", moss)
    assert tags(html, "del") == ["open,"] and tags(html, "ins") == ["closed,"]
    assert "[MOSS] items[1].caption" in html
    assert "with her mouth <del" in html                                   # 元の位置（前後の語に挟まれる）


def test_bbox_old_is_deleted_style_and_new_is_inserted_style(base):
    moss = moss_with(base, lambda m: m["items"][2].update(bbox=[-380, -190, 320, 200]))
    html = html_of(base, "moss", moss)
    assert tags(html, "del") == ["[-380, -190, 320, 190]"] and tags(html, "ins") == ["[-380, -190, 320, 200]"]


def test_item_added_and_removed(base):
    gone = html_of(base, "moss", moss_with(base, lambda m: m["items"].pop(0)))
    assert "items[0]（item の削除）" in gone and len(tags(gone, "del")) == 1 and not tags(gone, "ins")
    new = {"label": "object", "caption": "A small dog.", "bbox": [0, 0, 10, 10]}
    added = html_of(base, "moss", moss_with(base, lambda m: m["items"].append(new)))
    assert "items[4]（item の追加）" in added and "A small dog." in tags(added, "ins")[0]


def test_broken_json_is_shown_as_line_diff(base):
    html = html_of(base, "moss", base.moss_prompt.replace("Elesa", "Elesa!")[:-2])
    assert "JSON を解析できないため行単位" in html
    assert tags(html, "del") and tags(html, "ins")
    assert any("Elesa as she" in d for d in tags(html, "del")) and any("Elesa! as she" in d for d in tags(html, "ins"))


def test_text_and_html_come_from_the_same_rows(base):
    anima = base.anima_prompt + ", sweat"
    rows = field_rows(base, "anima", anima)
    assert "\n".join(to_text(r) for r in rows) in build_diff(base, {"anima": anima}, SCENE)


def test_generated_note_is_shown_in_the_html(base):
    t = split_tags(base.anima_prompt)
    t[t.index("safe")] = "sensitive"
    assert "エディタでは直せない" in html_of(base, "anima", ", ".join(t))


# ---- 表示の出し分け ----
def test_unedited_fields_have_no_diff_html(base):
    qe = Q.refresh(Q.new_qe(), base, KEY, SCENE)[0]
    assert Q.field_diff_html(qe) == {f: "" for f in Q.FIELDS}
    qe = Q.set_edits(qe, {"anima": base.anima_prompt + ", sweat"})
    html = Q.field_diff_html(qe)
    assert html["anima"] and not html["moss"] and not html["negm"] and not html["nega"]


def test_negative_edit_is_shown_even_when_apply_is_off(repo):
    r = compile_sample(repo, SCENE, apply_negative=False)
    qe = Q.set_edits(Q.refresh(Q.new_qe(), r, KEY, SCENE)[0], {"nega": r.negative_anima + ", extra"})
    assert "extra" in Q.field_diff_html(qe)["nega"]


def test_reset_and_returning_to_the_same_text_hide_the_diff(base):
    qe = Q.set_edits(Q.refresh(Q.new_qe(), base, KEY, SCENE)[0], {"anima": "x", "negm": "y"})
    assert Q.field_diff_html(qe)["anima"] and Q.field_diff_html(qe)["negm"]
    assert Q.field_diff_html(Q.reset_field(qe, "anima"))["anima"] == ""
    assert Q.field_diff_html(Q.set_edits(qe, {"negm": base.negative_moss}))["negm"] == ""


def test_recompile_clears_the_diff_html(repo, base):
    qe = Q.set_edits(Q.refresh(Q.new_qe(), base, KEY, SCENE)[0], {"anima": base.anima_prompt + ", sweat"})
    assert Q.field_diff_html(qe)["anima"]
    other = compile_sample(repo, SCENE, cast={**repo.scene(SCENE).cast, "center": CastEntry("skyla_guilty", None)})
    qe2, reset = Q.refresh(qe, other, KEY, SCENE)
    assert reset and Q.field_diff_html(qe2) == {f: "" for f in Q.FIELDS}


# ---- エスケープ ----
EVIL = '<script>alert("x")</script> & <b>'


def test_special_characters_are_escaped_everywhere(base):
    html = html_of(base, "anima", base.anima_prompt + f", {EVIL}")
    assert "<script" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b&gt;" in html
    moss = moss_with(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("mouth open,", "mouth a<b & \"c\" 'd',")))
    html = html_of(base, "moss", moss)
    assert "a&lt;b" in html and "&amp;" in html and "&quot;c&quot;" in html and "&#x27;d&#x27;" in html
    assert "a<b" not in html


def test_diff_markers_in_user_text_do_not_break_the_html(base):
    html = html_of(base, "anima", base.anima_prompt + ", [-x-]{+y+}")
    assert tags(html, "ins") == ["[-x-]{+y+}"] and tags(html, "del") == []


def test_origin_labels_are_escaped_too():
    from moa.core.quickdiff import Row
    html = rows_to_html([Row("ins", "t", note="   ← Action <x&y> の anima")])
    assert "&lt;x&amp;y&gt;" in html and "<x&y>" not in html


# ---- 文字サイズ ----
def test_prompt_css_uses_the_font_size_and_only_our_classes():
    css = prompt_css(20)
    assert "font-size: 20px !important" in css and "line-height" in css
    selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", css) for s in block.split(",")]
    assert all(".moa-" in s for s in selectors), selectors              # ほかの部品には効かない
    assert "font-size: 16px" in prompt_css()


def test_config_prompt_font_size(tmp_path):
    cfg = lambda text: write(tmp_path / "c.yaml", text)               # noqa: E731
    assert config_prompt_font_size(cfg("builder: {promptFontSize: 20}\n")) == 20
    assert config_prompt_font_size(cfg("builder: {promptFontSize: 18.5}\n")) == 18.5
    assert config_prompt_font_size(cfg("builder: {port: 7860}\n")) == 16
    for bad in ("7", "49", "big", "true", "null"):
        assert config_prompt_font_size(cfg(f"builder: {{promptFontSize: {bad}}}\n")) == 16
    assert config_prompt_font_size(tmp_path / "missing.yaml") == 16
    assert config_prompt_font_size(tmp_path / "missing.yaml", 12) == 12


def test_real_config_has_the_default():
    from conftest import ROOT
    assert config_prompt_font_size(ROOT / "config.yaml") == 16


def test_build_ui_applies_the_font_size_to_the_css(repo):
    demo = build_ui(repo, prompt_font_px=22)
    assert "font-size: 22px" in demo.css and ".moa-prompt" in demo.css
    assert "font-size: 16px" in build_ui(repo).css
