"""クイック編集（SPEC 10.1章）：送信内容、記録しないこと、差分、リセット、Anima 段の再試行。"""
import json

import pytest

from conftest import compile_sample
from moa.comfy import differences, iter_retry_anima, iter_run
from moa.core import CastEntry
from moa.core.quickdiff import build_diff
from moa.core.textutil import split_tags
from moa.ui import logic as L
from moa.ui import quickedit as Q
from test_comfy import ANIMA_OK, MOSS_OK, FakeClient, cfg, cfg_path, ev, gen_of  # noqa: F401  (fixtures)

SCENE = "beach_chat__trio"


@pytest.fixture
def base(repo):
    r = compile_sample(repo, SCENE)
    assert r.ok
    return r


def moss_with(base, fn):
    m = json.loads(base.moss_prompt)
    fn(m)
    return json.dumps(m, indent=2, ensure_ascii=False)


def anima_without(base, tag, nth):
    tags = split_tags(base.anima_prompt)
    idx = [i for i, t in enumerate(tags) if t == tag][nth]
    del tags[idx]
    return ", ".join(tags)


def node(cfg_, binding, wf):
    return wf[binding.node]["inputs"][binding.input]


# ---- 送信（13.2 / 13.3章）----
def test_edited_strings_are_sent_to_the_workflow(cfg, base):
    moss = moss_with(base, lambda m: m["items"][1].update(caption="Edited caption."))
    anima = base.anima_prompt + ", sweat"
    sent, fields = Q.apply_quick_edit(base, {"moss": moss, "anima": anima})
    assert fields == ["moss", "anima"]
    fc = FakeClient(MOSS_OK + ANIMA_OK)
    list(iter_run(cfg, sent, client=fc, seed=1))
    wf = fc.submitted[0]
    assert node(cfg, cfg.moss_positive, wf) == moss
    assert node(cfg, cfg.anima_positive, wf) == anima


def test_unedited_fields_are_not_replaced(cfg, base):
    sent, fields = Q.apply_quick_edit(base, {f: v for f, v in Q.base_values(base).items()})
    assert fields == [] and sent == base


def test_edited_negatives_are_sent_only_when_apply_negative_is_on(cfg, repo):
    edited = {"negm": "my moss negative", "nega": "my anima negative"}
    off = compile_sample(repo, SCENE, apply_negative=False)
    sent, fields = Q.apply_quick_edit(off, edited)
    assert fields == []                                          # OFF のときは編集しても送らない
    fc = FakeClient(MOSS_OK + ANIMA_OK)
    list(iter_run(cfg, sent, client=fc, seed=1))
    default = json.loads(json.dumps(cfg.workflow))
    assert node(cfg, cfg.moss_negative, fc.submitted[0]) == node(cfg, cfg.moss_negative, default)
    assert node(cfg, cfg.anima_negative, fc.submitted[0]) == node(cfg, cfg.anima_negative, default)

    on = compile_sample(repo, SCENE, apply_negative=True)
    sent, fields = Q.apply_quick_edit(on, edited)
    assert fields == ["negm", "nega"]
    fc = FakeClient(MOSS_OK + ANIMA_OK)
    list(iter_run(cfg, sent, client=fc, seed=1))
    assert node(cfg, cfg.moss_negative, fc.submitted[0]) == "my moss negative"
    assert node(cfg, cfg.anima_negative, fc.submitted[0]) == "my anima negative"


def test_negative_edit_is_annotated_when_apply_is_off(base):
    qe = Q.refresh(Q.new_qe(), base, (0, "a", "b"), "s")[0]
    qe = Q.set_edits(qe, {"negm": "x"})
    assert Q.NEG_OFF_NOTE in Q.statuses(qe, False)[2]
    assert Q.statuses(qe, True)[2] == Q.EDITED
    assert Q.statuses(qe, False)[0] == ""


def test_broken_moss_json_stops_generation(cfg, base):
    with pytest.raises(Q.QuickEditError, match="MOSS JSON"):
        Q.apply_quick_edit(base, {"moss": base.moss_prompt[:-3]})
    # 壊れていても、MOSS を編集していなければ止まらない
    Q.apply_quick_edit(base, {"anima": base.anima_prompt + ", x"})


# ---- 記録しない（13.4章）----
def opened(repo):
    sc, nav, _ = L.apply_entry(repo, L.new_sc(), L.new_nav(), repo.entry("scene", SCENE))
    return sc, nav


def test_a_quick_edit_generation_is_not_recorded_on_save(repo, cfg, base):
    sc, nav = opened(repo)
    sent, fields = Q.apply_quick_edit(base, {"anima": base.anima_prompt + ", sweat"})
    snap = gen_of(iter_run(cfg, sent, client=FakeClient(MOSS_OK + ANIMA_OK), seed=5)).snapshot()
    snap["quick_edit"] = Q.quick_info(base, fields)
    _, msg = L.save(repo, sc, nav, "qe_trial", "", snap)
    assert "試し打ち" in msg and "記録していません" in msg
    compiled = repo.entry("scene", "qe_trial").raw["compiled"]
    assert not compiled.get("images") and compiled.get("seed") is None
    assert compiled["animaPrompt"] == base.anima_prompt          # 編集は compiled にも入らない


def test_a_normal_generation_is_recorded_on_save(repo, cfg, base):
    sc, nav = opened(repo)
    snap = gen_of(iter_run(cfg, base, client=FakeClient(MOSS_OK + ANIMA_OK), seed=5)).snapshot()
    L.save(repo, sc, nav, "normal_trial", "", snap)
    assert repo.entry("scene", "normal_trial").raw["compiled"]["seed"] == 5


# ---- 差分 ----
def lines_of(text):
    return text.splitlines()


def test_diff_format_header_and_unsaved_scene(base):
    d = build_diff(base, {"anima": base.anima_prompt + ", sweat"}, None)
    assert lines_of(d)[:3] == ["=== MOA quick-edit diff ===", "scene: (未保存) / action: beach_chat / background: beach",
                               "[Anima]"]
    assert "scene: beach_chat__trio / action" in build_diff(base, {"anima": "x"}, SCENE)


def test_anima_added_tag_gets_the_owner_of_the_insert_position(base):
    tags = split_tags(base.anima_prompt)
    i = tags.index("standing")                                   # Action の anima の先頭
    tags.insert(i + 1, "sweat")
    d = build_diff(base, {"anima": ", ".join(tags)}, SCENE)
    assert "+ sweat   ← Action beach_chat の anima" in d
    assert "\n- " not in d


@pytest.mark.parametrize("tag, nth, owner", [("huge breasts", 0, "skyla_bw"), ("huge breasts", 1, "elesa_bw"),
                                              ("huge breasts", 2, "hex_maniac_xy"), ("pokemon", 1, "elesa_bw")])
def test_repeated_tags_are_distinguished_by_position(base, tag, nth, owner):
    d = build_diff(base, {"anima": anima_without(base, tag, nth)}, SCENE)
    removed = [ln for ln in lines_of(d) if ln.startswith("- ")]
    assert len(removed) == 1 and removed[0].startswith(f"- {tag}   ← Character {owner} の anima"), removed


def test_moss_word_diff_with_owner(base):
    moss = moss_with(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("mouth open,", "mouth closed,")))
    d = build_diff(base, {"moss": moss}, SCENE)
    assert "[MOSS] items[1].caption" in d
    assert "[-open,-]{+closed,+}" in d
    assert "←  Action" not in d and "← Action beach_chat の mossItems[0].caption" in d


def test_moss_edit_inside_a_character_block_points_to_the_character(base):
    moss = moss_with(base, lambda m: m["items"][1].update(
        caption=m["items"][1]["caption"].replace("auburn", "black")))
    d = build_diff(base, {"moss": moss}, SCENE)
    assert "[-auburn-]{+black+}" in d and "← Character skyla_bw の moss（{{left}}）" in d


def test_moss_bbox_is_old_to_new(base):
    moss = moss_with(base, lambda m: m["items"][2].update(bbox=[-380, -190, 320, 200]))
    d = build_diff(base, {"moss": moss}, SCENE)
    assert ("[MOSS] items[2].bbox\n  [-380, -190, 320, 190] → [-380, -190, 320, 200]"
            "   ← Action beach_chat の mossItems[1].bbox") in d


def test_moss_item_removed_and_added(base):
    gone = build_diff(base, {"moss": moss_with(base, lambda m: m["items"].pop(0))}, SCENE)
    assert "[MOSS] items[0]（item の削除）" in gone and "← Background beach の mossItems[0]" in gone
    new = {"label": "object", "caption": "A small dog.", "bbox": [0, 0, 10, 10]}
    added = build_diff(base, {"moss": moss_with(base, lambda m: m["items"].append(new))}, SCENE)
    assert "[MOSS] items[4]（item の追加）" in added and '"A small dog."' in added


def test_moss_edit_among_removed_item_stays_a_word_diff(base):
    def fn(m):
        m["items"].pop(1)
        m["items"][1]["caption"] += " Sweat."
    d = build_diff(base, {"moss": moss_with(base, fn)}, SCENE)
    assert "（item の削除）" in d and "{+Sweat.+}" in d


def test_broken_moss_json_falls_back_to_line_diff_with_owners(base):
    d = build_diff(base, {"moss": base.moss_prompt.replace("beach_chat", "x")[:-2].replace("Elesa", "Elesa!")}, SCENE)
    assert "[MOSS]（JSON を解析できないため行単位）" in d
    assert any(ln.startswith("  - ") and "Elesa as she" in ln and "← Character elesa_bw の moss（{{center}}）" in ln
               for ln in lines_of(d))
    assert any(ln.startswith("  + ") and "Elesa! as she" in ln for ln in lines_of(d))


def test_compiler_generated_parts_carry_a_note(base):
    note = "エディタでは直せない。rating／extras／sex で変える"
    tags = split_tags(base.anima_prompt)
    tags[tags.index("safe")] = "sensitive"
    d = build_diff(base, {"anima": ", ".join(tags)}, SCENE)
    assert "- safe   ← 生成（rating）" in d and note in d
    d = build_diff(base, {"moss": base.moss_prompt.replace('"single"', '"double"')}, SCENE)
    assert "生成（canvas）" in d and note in d
    d = build_diff(base, {"anima": base.anima_prompt.replace("3girls", "4girls")}, SCENE)
    assert "生成（people）" in d and note in d


def test_user_written_parts_have_no_note(base):
    d = build_diff(base, {"anima": base.anima_prompt + ", sweat"}, SCENE)
    assert "エディタでは直せない" not in d


def test_negative_diff_has_owners(repo):
    r = compile_sample(repo, SCENE, apply_negative=True)
    tags = split_tags(r.negative_anima)
    d = build_diff(r, {"nega": ", ".join(tags + ["extra"]), "negm": ", ".join(split_tags(r.negative_moss)[1:])}, SCENE)
    assert "[Negative MOSS]" in d and "[Negative Anima]" in d
    assert "- worst quality   ← 生成（negativeBase）" not in d     # nega は末尾に足しただけ
    assert "- worst quality, " not in d
    assert "+ extra   ← Action beach_chat の negativeAnima" in d


# ---- 再コンパイルでのリセット ----
def key_of(sc):
    return (sc["open_seq"], sc["action_id"], sc["background_id"])


def test_recompile_resets_edits_and_keeps_the_discarded_diff(repo, base):
    qe, reset = Q.refresh(Q.new_qe(), base, (1, "beach_chat", "beach"), SCENE)
    assert not reset
    qe = Q.set_edits(qe, {"anima": base.anima_prompt + ", sweat"})
    assert Q.diff_view(qe).startswith("=== MOA quick-edit diff ===")
    other = compile_sample(repo, SCENE, cast={**repo.scene(SCENE).cast, "center": CastEntry("skyla_guilty", None)})
    qe2, reset = Q.refresh(qe, other, (1, "beach_chat", "beach"), SCENE)
    assert reset and qe2["edits"] == {}
    assert qe2["discarded"].startswith("# 破棄された編集の差分\n=== MOA quick-edit diff ===")
    assert "+ sweat" in qe2["discarded"]
    assert Q.diff_view(qe2) == qe2["discarded"]                  # 現在の差分はなく、破棄された差分だけが残る
    assert Q.shown_values(qe2)["anima"] == other.anima_prompt


def test_changing_action_or_background_resets_even_if_the_text_is_the_same(base):
    qe = Q.refresh(Q.new_qe(), base, (1, "a", "b"), SCENE)[0]
    qe = Q.set_edits(qe, {"anima": "x"})
    qe2, reset = Q.refresh(qe, base, (1, "a", "other_bg"), SCENE)
    assert reset and qe2["discarded"]


def test_toggling_negative_or_saving_keeps_the_edits(repo, base):
    qe = Q.refresh(Q.new_qe(), base, (1, "a", "b"), SCENE)[0]
    qe = Q.set_edits(qe, {"anima": "x"})
    toggled = compile_sample(repo, SCENE, apply_negative=True)     # 文字列は変わらない
    qe2, reset = Q.refresh(qe, toggled, (1, "a", "b"), SCENE)
    assert not reset and qe2["edits"] == {"anima": "x"} and not qe2["discarded"]
    qe3, reset = Q.refresh(qe2, toggled, (1, "a", "b"), "saved_id")   # 保存で scene id が付いても残る
    assert not reset and qe3["label"] == "saved_id"


def test_resetting_a_field_and_restoring_the_same_text_clear_the_edit(base):
    qe = Q.refresh(Q.new_qe(), base, (1, "a", "b"), SCENE)[0]
    qe = Q.set_edits(qe, {"anima": "x", "negm": "y"})
    assert set(qe["edits"]) == {"anima", "negm"}
    assert set(Q.reset_field(qe, "anima")["edits"]) == {"negm"}
    assert Q.set_edits(qe, {"anima": base.anima_prompt})["edits"] == {"negm": "y"}


# ---- Anima 段だけの再試行 ----
def test_retry_uses_the_strings_sent_the_first_time(cfg, base):
    first_anima = base.anima_prompt + ", sweat"
    sent, fields = Q.apply_quick_edit(base, {"anima": first_anima})
    quick = Q.quick_info(base, fields)
    fail = FakeClient(MOSS_OK + [ev("execution_error", node_id="16", exception_message="boom")])
    snap = gen_of(iter_run(cfg, sent, client=fail, seed=777)).snapshot()
    snap["quick_edit"] = quick
    assert snap["anima_prompt"] == first_anima

    # その後に欄を別の文字列へ編集しても、再試行には使わない（再試行は欄の値を受け取らない）
    result_now = base
    retry = Q.retry_result(result_now, snap)
    assert retry.anima_prompt == first_anima and retry.moss_prompt == base.moss_prompt
    assert differences(snap, result_now, None) == []             # 編集前のコンパイル結果と同じなら再試行できる
    fc = FakeClient(ANIMA_OK)
    list(iter_retry_anima(cfg, retry, snap, client=fc))
    assert node(cfg, cfg.anima_positive, fc.submitted[0]) == first_anima


def test_retry_is_refused_when_the_compile_result_changed(cfg, repo, base):
    sent, fields = Q.apply_quick_edit(base, {"anima": base.anima_prompt + ", sweat"})
    snap = gen_of(iter_run(cfg, sent, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)).snapshot()
    snap["quick_edit"] = Q.quick_info(base, fields)
    other = compile_sample(repo, SCENE, cast={**repo.scene(SCENE).cast, "center": CastEntry("skyla_guilty", None)})
    assert "animaPrompt" in differences(snap, other, None)
