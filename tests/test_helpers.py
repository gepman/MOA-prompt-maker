"""小さな部品：textutil、人数タグ（7.4章）、bbox 判定（9.3章）、gen。"""
import pytest

from moa.core.bbox import check_inversion, check_overflow
from moa.core.gen import clean_gen, normalize_value
from moa.core.models import MossItem
from moa.core.people import count_tag, is_template_count_tag, people_tags, strip_template_count_tags
from moa.core.textutil import clean_tags, collapse_ws, dedupe_tags, join_negative, natural_key, strip_block


# ---- textutil ----
def test_strip_block_and_collapse():
    assert strip_block("  a b \n") == "a b" and strip_block(None) == ""
    assert collapse_ws(" a \n  b\t c ") == "a b c"


def test_clean_tags_keeps_duplicates_drops_empties():
    assert clean_tags(" a, , b ,, a,\n") == "a, b, a"
    assert clean_tags("") == "" and clean_tags(None) == ""


def test_dedupe_is_case_insensitive_first_wins():
    assert dedupe_tags(["Blurry", "text", "blurry", "TEXT", "x"]) == ["Blurry", "text", "x"]
    assert join_negative(["a, B", None, "b, c"]) == "a, B, c"


def test_natural_key_reads_numbers():
    assert sorted(["10_b", "2_a", "1_z"], key=natural_key) == ["1_z", "2_a", "10_b"]


# ---- 人数タグ ----
@pytest.mark.parametrize("n,word,expected", [
    (1, "girl", "1girl"), (2, "girl", "2girls"), (5, "girl", "5girls"), (6, "girl", "6+girls"),
    (12, "girl", "6+girls"), (1, "boy", "1boy"), (3, "boy", "3boys"), (6, "boy", "6+boys"),
])
def test_count_tag(n, word, expected):
    assert count_tag(n, word) == expected


@pytest.mark.parametrize("girls,boys,others,expected", [
    (1, 0, 0, ["1girl", "solo"]), (0, 1, 0, ["1boy", "solo"]), (0, 0, 1, ["solo"]),
    (2, 1, 0, ["2girls", "1boy"]), (1, 1, 0, ["1girl", "1boy"]), (1, 0, 3, ["1girl"]),
    (0, 0, 0, []), (0, 0, 3, []),
])
def test_people_tags(girls, boys, others, expected):
    assert people_tags(girls, boys, others) == expected


@pytest.mark.parametrize("tag,expected", [
    ("3girls", True), ("1girl", True), ("2boys", True), ("1BOY", True), ("6+girls", True), ("10boys", True),
    ("solo", True), ("SOLO", True), (" solo ", True),
    ("3children", False), ("2people", False), ("girls", False), ("multiple girls", False),
    ("solo focus", False), ("huge breasts", False), ("{{left}}", False),
])
def test_is_template_count_tag(tag, expected):
    assert is_template_count_tag(tag) is expected


def test_strip_template_count_tags_keeps_everything_else_untouched():
    cleaned, removed = strip_template_count_tags("3girls, {{left}}, solo, 1boy, standing, 3children")
    assert removed == ["3girls", "solo", "1boy"]
    assert [t.strip() for t in cleaned.split(",")] == ["{{left}}", "standing", "3children"]
    assert strip_template_count_tags("a, b") == ("a, b", [])


# ---- bbox ----
def item(*bbox, label="object"):
    return MossItem(label, "x", list(bbox))


CANVAS = {"w": 1536, "h": 1024}   # 有効範囲 x ∈ [-768, 768]、y ∈ [-512, 512]


def test_bbox_within_canvas_has_no_warning():
    assert check_overflow([item(-512, -768, 512, 768)], CANVAS, "Background 'b'") == []   # ちょうど端は範囲内


@pytest.mark.parametrize("bbox,fragment", [
    ((-512, -769, 0, 0), "左 1px"),
    ((-512, 0, 0, 770), "右 2px"),
    ((-515, 0, 0, 0), "上 3px"),
    ((0, 0, 516, 0), "下 4px"),
])
def test_bbox_overflow_reports_each_side(bbox, fragment):
    issues = check_overflow([item(*bbox)], CANVAS, "Action 'a'")
    assert len(issues) == 1 and issues[0].level == "warning"
    assert fragment in issues[0].message and "1番目" in issues[0].message and "完全にはみ出し" not in issues[0].message


def test_bbox_overflow_multiple_sides_and_position():
    issues = check_overflow([item(0, 0, 10, 10), item(-600, -800, 600, 800, label="face")], CANVAS, "Action 'a'")
    assert len(issues) == 1                                   # 2番目だけ
    m = issues[0].message
    assert "2番目" in m and "label: face" in m
    assert all(s in m for s in ("左 32px", "右 32px", "上 88px", "下 88px"))


def test_bbox_fully_outside_is_worded_differently():
    (issue,) = check_overflow([item(600, 0, 700, 100)], CANVAS, "Action 'a'")
    assert "完全にはみ出し" in issue.message and "下" in issue.message


def test_bbox_odd_canvas_uses_half_pixels():
    (issue,) = check_overflow([item(0, -50, 0, 0)], {"w": 99, "h": 99}, "Action 'a'")
    assert "左 0.5px" in issue.message


def test_bbox_inversion_is_an_error_and_not_an_overflow():
    items = [item(10, 0, 0, 5), item(0, 10, 5, 0)]
    errs = check_inversion(items, "Action 'a'")
    assert [e.level for e in errs] == ["error", "error"] and "2番目" in errs[1].message
    assert check_overflow([item(900, 0, -900, 0)], CANVAS, "Action 'a'") == []   # 逆転は別途エラーにする


# ---- gen ----
def test_gen_helpers():
    assert normalize_value("steps", 14.0) == 14 and isinstance(normalize_value("steps", 14.0), int)
    assert normalize_value("cfg", 1.80000001) == 1.8
    assert clean_gen({"anima": {"denoise": 0.45, "steps": 14, "bogus": 1}, "moss": {}, "other": {"x": 1}}) == \
        {"anima": {"steps": 14, "denoise": 0.45}}
