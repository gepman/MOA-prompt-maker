"""エディタの文字拡大と色付き差分表示（EDITOR_SPEC 14章）。"""
import re

import pytest

from conftest import write
from moa.core import diffcore, quickdiff
from moa.core.appconfig import config_prompt_font_size
from moa.core.diffcore import opcodes, to_text
from moa.core.textutil import split_tags
from moa.editor.logic import diffs as D
from moa.editor.logic import forms, session
from moa.editor.logic.forms import MAX_ITEMS
from moa.editor.ui.app import build_editor
from moa.ui import diffhtml
from test_editor_build import Driver


def entry_state(repo, kind, eid):
    return session.open_entry(repo, session.new_state(), repo.entry(kind, eid))


def tags(html, cls):
    tag = "del" if cls == "del" else "ins"
    return re.findall(rf'<{tag} class="moa-{cls}(?: moa-jump)?"[^>]*>(.*?)</{tag}>', html)


# ---- 基準（14.2）----
def test_baseline_when_opened_is_the_loaded_content(repo):
    st = entry_state(repo, "character", "skyla_bw")
    assert D.baseline(st) == st["loaded"] == forms.values_of(repo.character("skyla_bw"))
    assert D.change_rows("character", st["loaded"], D.baseline(st)) == []
    edited = {**st["loaded"], "name": "新しい名前"}
    assert [to_text(r) for r in D.change_rows("character", edited, D.baseline(st))][:2] == [
        "name", f"  {st['loaded']['name']} → 新しい名前"]


def test_baseline_for_new_is_the_initial_values(repo):
    st = session.start_new(repo, {**session.new_state(), "section": "actions", "cwd": "actions"})
    assert D.baseline(st) == forms.new_values("action")
    typed = {**forms.new_values("action"), "name": "海辺で談笑", "slots": ["left", "right"]}
    keys = [r.text for r in D.change_rows("action", typed, D.baseline(st)) if r.kind == "head"]
    assert keys == ["name", "slots"]


def test_baseline_for_copy_is_the_saved_source_not_the_unsaved_edits(repo):
    st = entry_state(repo, "character", "skyla_bw")
    saved_name = st["loaded"]["name"]
    unsaved = {**st["loaded"], "name": "未保存の編集", "moss": st["loaded"]["moss"] + " 追記"}
    cp = session.start_copy(repo, st, unsaved)
    assert cp["mode"] == "copy" and cp["loaded"]["name"] == "未保存の編集"      # フォームには編集が入る
    assert D.baseline(cp)["name"] == saved_name and D.baseline(cp)["id"] == "skyla_bw_copy"
    keys = [r.text for r in D.change_rows("character", cp["loaded"], D.baseline(cp)) if r.kind == "head"]
    assert keys == ["name", "moss"]                                    # 編集は差分として見える。ID は対象外


def test_baseline_after_save_is_the_saved_content(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/skyla_bw.yaml")
    d["name"] = "保存する名前"
    d.call("diff_name")
    assert d.update_of("chg_html")["visible"]
    d.call("save")
    assert d["st"]["base"] is None and d["st"]["loaded"]["name"] == "保存する名前"
    assert D.change_rows("character", d["st"]["loaded"], D.baseline(d["st"])) == []
    assert d.update_of("chg_html")["visible"] is False                  # 保存したら消える


def test_baseline_after_reload_is_the_reread_content(repo, data_dir):
    st = entry_state(repo, "character", "skyla_bw")
    path = data_dir / "characters" / "skyla_bw.yaml"
    text = path.read_text(encoding="utf-8")
    write(path, text.replace(f"name: {st['loaded']['name']}", "name: 外部で変更された名前", 1))
    st2 = session.reload_current(repo, st)
    assert D.baseline(st2)["name"] == "外部で変更された名前" and st2["base"] is None
    assert D.change_rows("character", st2["loaded"], D.baseline(st2)) == []


# ---- 長い文章・タグの欄（14.3）----
def test_anima_is_compared_by_tag_in_order(repo):
    old = "masterpiece, huge breasts, red hair, huge breasts, smile, huge breasts"
    new = "masterpiece, huge breasts, red hair, smile, huge breasts"             # 2番目の huge breasts を消した
    rows = D.text_rows("anima", old, new)
    assert [(r.kind, r.text) for r in rows] == [("del", "huge breasts")]
    assert opcodes(split_tags(old), split_tags(new))[1][:3] == ("delete", 3, 4)   # 位置は 4 番目（先頭側の一致を優先）
    rows = D.text_rows("anima", "a, b, c", "a, x, b, c, y")
    assert [(r.kind, r.text) for r in rows] == [("ins", "x"), ("ins", "y")]


def test_other_text_fields_are_compared_by_word(repo):
    rows = D.text_rows("mossCaption", "A sunny beach in the daytime.", "A sunny, calm beach in the daytime.")
    assert len(rows) == 1 and rows[0].kind == "inline"
    html = diffhtml.rows_to_html(rows)
    assert tags(html, "ins") == ["sunny, calm"] and tags(html, "del") == ["sunny"]


def test_whitespace_only_change_in_anima_still_shows(repo):
    assert D.text_rows("anima", "a, b", "a,   b")                       # タグは同じ：空白だけの違いも出す
    assert D.text_rows("moss", "a b", "a  b")                          # 単語は同じ：全体を旧→新で出す


def test_unchanged_fields_have_no_diff(repo):
    v = forms.values_of(repo.character("skyla_bw"))
    view = D.views("character", v, v)
    assert all(h == "" for h in view["fields"].values()) and view["changes"] == ""
    back = D.views("character", {**v, "anima": v["anima"] + ", x"}, v)
    assert back["fields"]["anima"] and back["changes"]
    restored = D.views("character", {**v, "anima": v["anima"] + ", x"} | {"anima": v["anima"]}, v)
    assert restored["fields"]["anima"] == "" and restored["changes"] == ""   # 値を戻すと消える


# ---- mossItems のカード ----
CARDS = [{"label": "object", "caption": "Skyla laughs with her mouth open.", "bbox": [-380, -640, 320, -260]},
         {"label": "object", "caption": "Elesa laughs with her head tilted back.", "bbox": [-380, -190, 320, 190]},
         {"label": "text", "caption": "A sign reads welcome.", "bbox": [0, 0, 100, 100]}]


def test_card_rows_show_caption_label_and_bbox(repo):
    new = {"label": "text", "caption": "Skyla laughs with her mouth closed.", "bbox": [-380, -640, 320, -200]}
    text = [to_text(r) for r in D.card_rows(CARDS[0], new)]
    assert "  object → text" in text and "  [-380, -640, 320, -260] → [-380, -640, 320, -200]" in text
    assert any("[-open.-]{+closed.+}" in ln for ln in text)
    assert D.card_rows(CARDS[0], CARDS[0]) == []


def test_cards_are_matched_to_the_baseline(repo):
    assert D.match_items(CARDS, CARDS) == [0, 1, 2]
    assert D.match_items(CARDS, [CARDS[1], CARDS[0], CARDS[2]]) == [1, 0, 2]                  # 並べ替え
    assert D.match_items(CARDS, CARDS[:2]) == [0, 1]                                          # 削除
    assert D.match_items(CARDS, CARDS + [{"label": "object", "caption": "", "bbox": [0, 0, 1, 1]}]) == [0, 1, 2, None]
    edited = {**CARDS[1], "caption": "Elesa laughs with her head tilted slightly back.", "bbox": [-1, -1, 1, 1]}
    assert D.match_items(CARDS, [CARDS[0], edited, CARDS[2]]) == [0, 1, 2]                    # 内容を変えても当たる
    assert D.match_items([CARDS[0]], [{"label": "object", "caption": "ぜんぜん別", "bbox": [0, 0, 1, 1]}]) == [0]   # 同じ位置


def test_items_rows_add_delete_and_reorder(repo):
    kinds = lambda rows: [(r.kind, r.text) for r in rows if r.kind == "head"]            # noqa: E731
    reorder = D.items_rows(CARDS, [CARDS[1], CARDS[0], CARDS[2]])
    assert ("head", "並べ替え") in kinds(reorder) and [r.text for r in reorder if r.kind == "pair"] == ["元 #2", "元 #1"]
    deleted = D.items_rows(CARDS, [CARDS[0], CARDS[2]])
    assert ("head", "元の #2（削除）") in kinds(deleted) and any(r.kind == "del" for r in deleted)
    new = {"label": "object", "caption": "A dog.", "bbox": [0, 0, 10, 10]}
    added = D.items_rows(CARDS, CARDS + [new])
    assert ("head", "#4（追加）") in kinds(added) and any(r.kind == "ins" for r in added)
    assert D.items_rows(CARDS, CARDS) == []


def test_card_views_mark_new_cards(repo):
    new = {"label": "object", "caption": "A dog.", "bbox": [0, 0, 10, 10]}
    views = D.card_views(CARDS, [CARDS[0], {**CARDS[1], "caption": "Elesa smiles."}, CARDS[2], new])
    assert views[0] == [] and views[2] == [] and views[1] and [r.text for r in views[3]] == ["新しく追加したカード"]


# ---- 短い項目（14.4）----
def test_short_items_show_old_to_new(repo):
    old = forms.values_of(repo.action("beach_chat"))
    new = {**old, "name": "海辺で雑談", "rating": "sensitive", "background": "white_background",
           "extras": {"male": 1}, "outfits": {**old["outfits"], "left": "none", "center": None}}
    text = "\n".join(to_text(r) for r in D.change_rows("action", new, old))
    assert f"name\n  {old['name']} → 海辺で雑談" in text
    assert "rating\n  safe → sensitive" in text and "background\n  beach → white_background" in text
    assert "extras\n  なし → male 1" in text
    assert "outfits.left\n  未指定 → none（着せない）" in text and "outfits.center\n  elesa_bw_summer → 未指定" in text
    b = forms.values_of(repo.background("beach"))
    text = "\n".join(to_text(r) for r in D.change_rows("background", {**b, "canvas": {"w": 1024, "h": 1024}}, b))
    assert "canvas\n  1536×1024 → 1024×1024" in text
    c = forms.values_of(repo.character("skyla_bw"))
    text = "\n".join(to_text(r) for r in D.change_rows("character", {**c, "sex": "male", "defaultOutfit": "none"}, c))
    assert "sex\n  female → male" in text and "defaultOutfit\n  skyla_pilot → none（着せない）" in text


def test_lists_are_compared_by_element(repo):
    old = forms.values_of(repo.action("beach_chat"))
    new = {**old, "tags": "日常, 海", "metaTags": [], "slots": ["left", "center", "right", "extra"]}
    text = "\n".join(to_text(r) for r in D.change_rows("action", new, old))
    assert "tags\n  + 海" in text and "metaTags\n  - group portrait" in text and "slots\n  + extra" in text
    assert "- 日常" not in text


def test_changes_keep_the_yaml_key_order_and_skip_the_id(repo):
    old = forms.values_of(repo.character("skyla_bw"))
    new = {**old, "id": "other", "anima": old["anima"] + ", x", "name": "N", "moss": old["moss"] + " y"}
    heads = [r.text for r in D.change_rows("character", new, old) if r.kind == "head"]
    assert heads == ["name", "moss", "anima"]


# ---- 表示（エスケープ）----
def test_special_characters_are_escaped(repo):
    old = forms.values_of(repo.character("skyla_bw"))
    evil = '<script>alert("x")</script> & <b>'
    new = {**old, "name": evil, "anima": old["anima"] + ", " + evil, "moss": old["moss"] + " " + evil}
    view = D.views("character", new, old)
    for html in [*view["fields"].values(), view["changes"]]:
        assert "<script" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b&gt;" in view["fields"]["anima"]
    assert "&lt;script&gt;" in view["changes"]
    card = D.card_views(CARDS, [{**CARDS[0], "caption": "<img src=x onerror=1>"}, *CARDS[1:]])
    assert "<img" not in diffhtml.rows_to_html(card[0]) and "&lt;img" in diffhtml.rows_to_html(card[0])


# ---- 画面 ----
def test_screen_shows_a_diff_after_blur_and_hides_it_when_restored(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/skyla_bw.yaml")
    original = d["anima"]
    assert d.update_of("df_anima")["visible"] is False                    # 開いた直後は出ない
    d["anima"] = original + ", sweat"
    d.call("diff_anima")
    up = d.update_of("df_anima")
    assert up["visible"] and tags(up["value"], "ins") == ["sweat"]
    assert d.update_of("chg_html")["visible"] and d.update_of("df_moss")["visible"] is False   # 編集していない欄は出ない
    d["anima"] = original
    d.call("diff_anima")
    assert d.update_of("df_anima")["visible"] is False and d.update_of("chg_html")["visible"] is False


def test_screen_radio_and_button_update_the_diff(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/skyla_bw.yaml")
    d["sex"] = "male"
    d.call("diff_sex")
    assert "female" in d.update_of("chg_html")["value"] and "male" in d.update_of("chg_html")["value"]
    d["name"] = "別名"
    d.call("diff")                                                         # ［差分を更新］
    assert "別名" in d.update_of("chg_html")["value"]


def test_screen_card_diffs_and_card_operations(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:actions/beach_chat.yaml")
    assert all(d.update_of(f"c_df{i}")["visible"] is False for i in range(3))
    d["c_caption1"] = d["c_caption1"] + " Sweat."
    d["c_y0" + "2"] = -100
    d.call("diff_c_caption1")
    assert d.update_of("c_df1")["visible"] and "Sweat." in d.update_of("c_df1")["value"]
    assert d.update_of("c_df0")["visible"] is False
    d.call("card_down_0")                                                  # カード操作でも更新される（並べ替え）
    assert "並べ替え" in d.update_of("chg_html")["value"]
    d.call("card_add")
    assert "追加" in d.update_of("chg_html")["value"] and d.update_of("c_df3")["visible"]


def test_screen_confirmation_boxes_show_the_changes(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:backgrounds/beach.yaml")                      # 使用中の Background（コピーオンライト）
    d["mossCaption"] = d["mossCaption"] + " A calm day."
    d.call("save")
    assert d["conf_diff"] and "A calm day." in d["conf_diff"] and "保存前の変更点" in d["conf_diff"]
    d2 = Driver(build_editor(repo))
    d2.call("init")
    d2.call("browse", "file:actions/beach_chat.yaml")                    # 使用中の Action
    d2["name"] = "海辺で雑談"
    d2.call("save")
    assert "海辺で雑談" in d2["conf_diff"]


def test_screen_diff_is_cleared_when_another_item_is_opened(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/skyla_bw.yaml")
    d["name"] = "変更"
    d.call("diff_name")
    d.call("browse", "file:characters/elesa_bw.yaml")                    # 未保存の変更で止まる
    d.call("discard")
    assert d.update_of("chg_html")["visible"] is False and d["conf_diff"] == ""


# ---- 文字サイズ（14.1）----
def test_font_size_priority_editor_then_builder_then_default(tmp_path):
    def cfg(text):
        return write(tmp_path / "c.yaml", text)
    assert config_prompt_font_size(cfg("builder: {promptFontSize: 20}\neditor: {promptFontSize: 24}\n"), section="editor") == 24
    assert config_prompt_font_size(cfg("builder: {promptFontSize: 20}\neditor: {port: 7863}\n"), section="editor") == 20
    assert config_prompt_font_size(cfg("editor: {port: 7863}\n"), section="editor") == 16
    assert config_prompt_font_size(cfg("builder: {promptFontSize: 20}\neditor: {promptFontSize: 99}\n"), section="editor") == 20   # 範囲外は次へ
    assert config_prompt_font_size(cfg("builder: {promptFontSize: x}\neditor: {promptFontSize: big}\n"), section="editor") == 16
    assert config_prompt_font_size(cfg("builder: {promptFontSize: 20}\neditor: {promptFontSize: 24}\n")) == 20     # section なし＝ビルダーの値
    assert config_prompt_font_size(tmp_path / "missing.yaml", section="editor") == 16


def test_real_config_keeps_the_default_font_size():
    from conftest import ROOT
    assert config_prompt_font_size(ROOT / "config.yaml", section="editor") == 16


def test_editor_css_uses_the_font_size_and_only_the_long_text_boxes_get_the_class(repo):
    demo = build_editor(repo, prompt_font_px=22)
    assert "font-size: 22px" in demo.css and ".moa-prompt" in demo.css
    classed = {n for n, comp in demo.named.items() if "moa-prompt" in (getattr(comp, "elem_classes", None) or [])}
    long_boxes = {"moss", "anima", "mossCaption", "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima",
                  *(f"c_caption{i}" for i in range(MAX_ITEMS))}
    diff_boxes = {"chg_html", "conf_diff", *(f"df_{k}" for k in D.LONG_KEYS), *(f"c_df{i}" for i in range(MAX_ITEMS))}
    assert classed == long_boxes | diff_boxes
    for name in ("id", "name", "tags", "group", "cw", "ch", "slots", "metaTags", "save_msg", "issues"):
        assert name not in classed


# ---- 共通部品（14章）----
def test_editor_and_builder_share_the_same_diff_code():
    assert D.tag_lines is diffcore.tag_lines is quickdiff.tag_lines
    assert D.word_diff is diffcore.word_diff is quickdiff.word_diff
    assert D.Row is diffcore.Row is quickdiff.Row
    assert D.rows_to_html is diffhtml.rows_to_html
    assert D.similar is diffcore.similar
    import moa.editor.logic.diffs as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "SequenceMatcher" not in src and "difflib" not in src             # 比較の処理はここに持たない
    # 同じ入力なら、ビルダーとエディタで同じ行ができる
    old, new = ["a", "b", "c"], ["a", "x", "c"]
    assert D.text_rows("anima", "a, b, c", "a, x, c") == diffcore.tag_lines(old, new, [], None, None)
