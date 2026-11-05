"""bbox 編集図（EDITOR_SPEC 8.1）の座標変換と、図 → カードの反映（Gradio に依存しない部分）。"""
import copy

import pytest

from moa.core.appconfig import config_bbox_margin
from moa.editor.logic import bboxedit as BE
from moa.editor.logic.bboxdraw import draw_base
from moa.editor.logic.forms import MAX_ITEMS

CANVAS = {"w": 1536, "h": 1024}
F = BE.make_frame(CANVAS, 0.5)   # 余白 768 / 512 → 土台 3072×2048、原点は (1536, 1024)


def item(bbox, label="object", caption=""):
    return {"label": label, "caption": caption, "bbox": list(bbox)}


def box(label, xmin, ymin, xmax, ymax):
    return {"label": label, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def fig_of(items, frame=F):
    """カードから作った図の四角（部品が返す値の模擬）。"""
    return BE.figure_boxes(items, frame)[0]


# ---- フレームと座標変換 ----
def test_frame_size_and_origin():
    assert (F.width, F.height, F.ox, F.oy) == (3072, 2048, 1536, 1024)
    assert F.canvas_rect == (768, 512, 2304, 1536)   # キャンバスの枠（土台のピクセル）
    f0 = BE.make_frame(CANVAS, 0)
    assert (f0.width, f0.height, f0.ox, f0.oy) == (1536, 1024, 768, 512)
    f1 = BE.make_frame({"w": 1024, "h": 1536}, 1)
    assert (f1.width, f1.height, f1.ox, f1.oy) == (3072, 4608, 1536, 2304)


@pytest.mark.parametrize("margin", [0, 0.5, 1, 0.33])
@pytest.mark.parametrize("canvas", [{"w": 1536, "h": 1024}, {"w": 1024, "h": 1536}, {"w": 832, "h": 1216},
                                    {"w": 1001, "h": 777}, {"w": 64, "h": 48}])
def test_round_trip_keeps_values(canvas, margin):
    f = BE.make_frame(canvas, margin)
    for bbox in ([-(f.h // 2), -(f.w // 2), f.h // 2, f.w // 2],             # ［全面］
                 [-(f.oy // 2), -(f.ox // 2), f.oy // 3, f.ox // 5], [0, 0, 1, 1],
                 [-f.oy, -f.ox, f.height - f.oy, f.width - f.ox]):             # 土台いっぱい
        assert BE.from_image(*BE.to_image(bbox, f), f) == list(bbox)


def test_to_image_known_values():
    assert BE.to_image([-512, -768, 512, 768], F) == (768, 512, 2304, 1536)   # ［全面］ = キャンバスの枠
    assert BE.to_image([0, 0, 10, 20], F) == (1536, 1024, 1556, 1034)          # 中心が原点
    assert BE.to_image([-1100, -1600, 1100, 1600], F) == (-64, -76, 3136, 2124)   # 土台の外でも計算はできる


def test_from_image_rounds_to_integers():
    assert BE.from_image(1536.4, 1024.6, 1556.5, 1034.49, F) == [1, 0, 10, 21]   # .5 は切り上げ
    assert all(isinstance(n, int) for n in BE.from_image(1536.2, 1024.2, 1556.7, 1034.7, F))


def test_from_image_sorts_and_clamps():
    assert BE.from_image(1556, 1034, 1536, 1024, F) == [0, 0, 10, 20]            # 大小が逆でも並べる
    assert BE.from_image(-50, -50, 99999, 99999, F) == [-1024, -1536, 1024, 1536]   # 土台の範囲に収める


# ---- 編集対象 ----
def test_editable_reasons():
    assert BE.editable([-512, -768, 512, 768], F) is None
    assert BE.editable([-1000, -1500, 1000, 1500], F) is None       # キャンバスの外（余白の中）は編集できる
    assert BE.editable([-1100, -1500, 1000, 1500], F) == BE.OUT_OF_RANGE   # 余白にも収まらない
    assert BE.editable([10, 0, 0, 5], F) == BE.REVERSED
    assert BE.editable([0, 10, 5, 0], F) == BE.REVERSED


def test_figure_boxes_numbers_and_notes():
    items = [item([-10, -20, 30, 40]), item([-5000, 0, 5, 5]), item([9, 9, 1, 1]), item([0, 0, 8, 8])]
    boxes, notes = BE.figure_boxes(items, F)
    assert [b["label"] for b in boxes] == ["#1", "#4"]                  # 番号はカードの番号のまま（欠番になる）
    assert (boxes[0]["xmin"], boxes[0]["ymin"], boxes[0]["xmax"], boxes[0]["ymax"]) == (1516, 1014, 1576, 1054)
    assert notes == ["#2 は図の範囲外のため、カードで編集してください", "#3 は大小が逆のため、カードで編集してください"]


# ---- 図 → カード ----
def test_untouched_figure_changes_nothing():
    items = [item([-10, -20, 30, 40], caption="a"), item([0, 0, 8, 8])]
    new, changed, notes = BE.apply_figure(items, fig_of(items), F)
    assert new == items and not changed and notes == []
    assert BE.figure_matches(items, fig_of(items), F)


def test_one_pixel_jitter_is_ignored():
    """部品は読み込み時に座標を 1px ずらして返すことがある。触っていない四角として扱う。"""
    items = [item([-10, -20, 30, 40])]
    jittered = [box("#1", 1516, 1013, 1576, 1054)]
    new, changed, _ = BE.apply_figure(items, jittered, F)
    assert new == items and not changed
    assert BE.figure_matches(items, jittered, F)
    assert not BE.figure_matches(items, [box("#1", 1516, 1012, 1576, 1054)], F)   # 2px 以上は別物


def test_move_writes_new_bbox_into_card():
    items = [item([-10, -20, 30, 40], caption="keep"), item([0, 0, 8, 8])]
    boxes = fig_of(items)
    boxes[0] = box("#1", 1616, 1064, 1676, 1104)       # 右へ +100、下へ +50 に移動（大きさは同じ）
    new, changed, _ = BE.apply_figure(items, boxes, F)
    assert changed and new[0] == item([40, 80, 80, 140], caption="keep")   # label・caption は変わらない
    assert new[1] == items[1]                                               # 触っていない四角は変わらない


def test_resize_corner_and_edge():
    items = [item([-10, -20, 30, 40])]
    new, _, _ = BE.apply_figure(items, [box("#1", 1516, 1014, 1700, 1100)], F)    # 右下の角を引く
    assert new[0]["bbox"] == [-10, -20, 76, 164]
    new, _, _ = BE.apply_figure(items, [box("#1", 1516, 900, 1576, 1054)], F)     # 上の辺を引く
    assert new[0]["bbox"] == [-124, -20, 30, 40]


def test_resize_through_the_opposite_side_is_sorted():
    items = [item([-10, -20, 30, 40])]
    new, _, _ = BE.apply_figure(items, [box("#1", 1700, 1100, 1516, 1014)], F)   # 角を反対側まで引き越した
    assert new[0]["bbox"] == [-10, -20, 76, 164]


def test_move_into_margin_is_allowed():
    items = [item([-10, -20, 30, 40])]
    new, changed, _ = BE.apply_figure(items, [box("#1", 2400, 1014, 2460, 1054)], F)   # キャンバスの右の外
    assert changed and new[0]["bbox"] == [-10, 864, 30, 924]     # x は +768 より外（はみ出し。警告は別に出る）


def test_draw_new_box_appends_item():
    items = [item([-10, -20, 30, 40], caption="a")]
    boxes = fig_of(items) + [box("", 1000, 600, 1400, 900)]     # ラベルの無い四角 = 図で描いたもの
    new, changed, notes = BE.apply_figure(items, boxes, F)
    assert changed and notes == [] and len(new) == 2
    assert new[1] == {"label": "object", "caption": "", "bbox": [-424, -536, -124, -136]}
    assert new[0] == items[0]
    assert not BE.figure_matches(new[:1], boxes, F)       # 描いた四角はラベル付きで描き直す必要がある
    assert BE.figure_matches(new, fig_of(new), F)


def test_drawn_box_with_a_clashing_label_is_still_new():
    """部品が、描いた四角に既存と同じラベル（#1）を付けても、既存の四角（座標が一致する側）は触っていない扱いになる。"""
    items = [item([-10, -20, 30, 40])]
    boxes = fig_of(items) + [box("#1", 1000, 600, 1400, 900)]
    new, changed, _ = BE.apply_figure(items, boxes, F)
    assert changed and len(new) == 2 and new[0] == items[0]
    assert new[1]["bbox"] == [-424, -536, -124, -136]


def test_drawing_is_ignored_at_max_items():
    items = [item([i, i, i + 5, i + 5]) for i in range(MAX_ITEMS)]
    boxes = fig_of(items) + [box("", 1000, 600, 1400, 900)]
    new, changed, notes = BE.apply_figure(items, boxes, F)
    assert not changed and new == items and len(new) == MAX_ITEMS
    assert notes == [f"最大{MAX_ITEMS}件です。図では追加できません"]
    assert not BE.figure_matches(new, boxes, F)           # 余分な四角は図から消す（描き直す）


def test_removing_a_box_in_the_figure_never_deletes_a_card():
    items = [item([-10, -20, 30, 40]), item([0, 0, 8, 8])]
    new, changed, _ = BE.apply_figure(items, fig_of(items)[1:], F)    # #1 の四角が図から消えた（Delete など）
    assert not changed and new == items
    assert not BE.figure_matches(items, fig_of(items)[1:], F)         # 図はカードの値で描き直す
    new, changed, _ = BE.apply_figure(items, [], F)
    assert not changed and new == items


def test_cards_not_editable_in_the_figure_keep_their_values():
    items = [item([-5000, 0, 5, 5]), item([9, 9, 1, 1]), item([-10, -20, 30, 40])]
    before = copy.deepcopy(items)
    boxes = fig_of(items)
    boxes[0] = box("#3", 1616, 1064, 1676, 1104)   # 編集できる #3 だけ動かす
    boxes += [box("#1", 5, 5, 50, 50), box("#2", 7, 7, 70, 70)]   # 万一、編集対象外のラベルの四角が来ても無視する
    new, changed, _ = BE.apply_figure(items, boxes, F)
    assert changed and new[0] == before[0] and new[1] == before[1]
    assert new[2]["bbox"] == [40, 80, 80, 140]
    assert items == before                                 # 渡したリストは書き換えない


def test_degenerate_box_is_ignored():
    items = [item([-10, -20, 30, 40])]
    new, changed, _ = BE.apply_figure(items, [box("#1", 1516, 1014, 1516, 1054)], F)   # 幅 0
    assert not changed and new == items
    new, changed, _ = BE.apply_figure(items, fig_of(items) + [box("", 100, 100, 100, 300)], F)   # 幅 0 の描画
    assert not changed and len(new) == 1


def test_malformed_boxes_are_ignored():
    items = [item([-10, -20, 30, 40])]
    new, changed, _ = BE.apply_figure(items, [{"label": "#1"}, None, "x", box("#1", "a", 0, 1, 1)], F)
    assert not changed and new == items


# ---- カードの変更 → 図 ----
def test_card_change_is_reflected_in_figure_boxes():
    items = [item([-10, -20, 30, 40])]
    items[0]["bbox"] = [0, 0, 100, 200]
    (b,) = fig_of(items)
    assert (b["xmin"], b["ymin"], b["xmax"], b["ymax"]) == (1536, 1024, 1736, 1124)
    items.append(item([-512, -768, 512, 768]))
    assert [b["label"] for b in fig_of(items)] == ["#1", "#2"]
    del items[0]
    assert [b["label"] for b in fig_of(items)] == ["#1"]           # 削除すると番号が詰まる


# ---- 土台の画像 ----
def test_draw_base_has_canvas_frame_and_outside_color():
    img = draw_base(F, [{"bbox": [-100, -100, 100, 100]}])
    assert img.size == (3072, 2048)
    assert img.getpixel((100, 100)) != img.getpixel((1000, 700))            # 余白とキャンバスで色が違う
    assert img.getpixel((1000, 700)) == (255, 255, 255)                      # キャンバスの中は白
    x0, y0, _, _ = F.canvas_rect
    assert img.getpixel((x0 + 2, 1000)) == (34, 34, 34)                      # キャンバスの枠は濃い線
    assert img.getpixel((1440, 1024)) == (157, 189, 230)                     # 参考線（薄い色）。左の辺 x=-100 の上


def test_draw_base_without_margin():
    img = draw_base(BE.make_frame({"w": 640, "h": 480}, 0))
    assert img.size == (640, 480)


# ---- 設定 ----
def test_config_bbox_margin(tmp_path):
    def cfg(text):
        p = tmp_path / "c.yaml"
        p.write_text(text, encoding="utf-8")
        return p

    assert config_bbox_margin(cfg("editor: {bboxMargin: 0.25}\n")) == 0.25
    assert config_bbox_margin(cfg("editor: {bboxMargin: 0}\n")) == 0
    assert config_bbox_margin(cfg("editor: {bboxMargin: 2}\n")) == 2
    for bad in ("3", "-0.1", "wide", "true", "null", "[1]"):
        assert config_bbox_margin(cfg(f"editor: {{bboxMargin: {bad}}}\n")) == 0.5
    assert config_bbox_margin(cfg("editor: {port: 1}\n")) == 0.5
    assert config_bbox_margin(cfg("builder: {bboxMargin: 1}\n")) == 0.5      # editor の値だけを見る
    assert config_bbox_margin(tmp_path / "missing.yaml") == 0.5
    assert config_bbox_margin(cfg("editor: [1\n")) == 0.5
    assert BE.make_frame(CANVAS, 5).mx == 768                                 # 無効な割合は既定
