"""bbox 編集図（EDITOR_SPEC 8.1）の画面側：部品から届く値を模した入力で、カード・保存状態・差分・図の表示用データを確かめる。"""
from conftest import write
from moa.core.repository import Repository
from moa.editor.logic import bboxedit as BE
from moa.editor.logic.forms import MAX_ITEMS, RECOMMENDED_ITEMS
from moa.editor.ui.app import build_editor
from test_editor_build import Driver

BBOX = ("y0", "x0", "y1", "x1")


def open_item(repo, section, path):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("section", section)
    d.call("browse", f"file:{path}")
    return d


def open_action(repo):
    return open_item(repo, "actions", "actions/beach_chat.yaml")   # 3件・推奨背景 beach（1536×1024、アイテム1件）


def cards(d):
    return [[d[f"c_{k}{i}"] for k in BBOX] for i in range(d["cards_n"])]


def fig(d):
    """図の表示用データ（いま部品に入っている値）。テストが書き換えても元に影響しないようコピーを返す。"""
    value = d["bbox_fig"]
    return {"image": value["image"], "boxes": [dict(b) for b in value["boxes"]]}


def send(d, boxes):
    """部品から change で届く値の模擬（マウスを離したとき）。"""
    return d.call("figure", {"image": None, "boxes": boxes})


def box(label, xmin, ymin, xmax, ymax):
    return {"label": label, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def test_opening_draws_boxes_with_card_numbers(repo):
    d = open_action(repo)
    value = fig(d)
    assert [b["label"] for b in value["boxes"]] == ["#1", "#2", "#3"]
    assert value["image"].size == (3072, 2048)                    # 1536×1024 + 余白 0.5
    b1 = value["boxes"][0]                                         # beach_chat #1 = [-380, -640, 320, -260]
    assert (b1["xmin"], b1["ymin"], b1["xmax"], b1["ymax"]) == (896, 644, 1276, 1344)
    assert d["bbox_fig_msg"] == ""


def test_move_in_figure_updates_card_status_and_diff(repo):
    d = open_action(repo)
    before = cards(d)
    boxes = fig(d)["boxes"]
    boxes[1] = box("#2", boxes[1]["xmin"] + 100, boxes[1]["ymin"] - 50, boxes[1]["xmax"] + 100, boxes[1]["ymax"] - 50)
    send(d, boxes)
    after = cards(d)
    assert after[1] == [before[1][0] - 50, before[1][1] + 100, before[1][2] - 50, before[1][3] + 100]
    assert after[0] == before[0] and after[2] == before[2]         # 触っていない四角は変わらない
    assert "未保存" in d["status"]
    chg = d.update_of("chg_html")
    assert chg["visible"] is True and "bbox" in chg["value"]          # 色付きの差分（保存前の変更点）に出る
    assert d.update_of("bbox_fig") is None                              # 移動だけなら図は描き直さない（ドラッグ後のちらつき防止）


def test_resize_in_figure(repo):
    d = open_action(repo)
    before = cards(d)
    boxes = fig(d)["boxes"]
    boxes[2] = box("#3", boxes[2]["xmin"], boxes[2]["ymin"], boxes[2]["xmax"] + 40, boxes[2]["ymax"] + 60)   # 右下の角を引く
    send(d, boxes)
    assert cards(d)[2] == [before[2][0], before[2][1], before[2][2] + 60, before[2][3] + 40]


def test_unchanged_or_jittered_figure_is_skipped(repo):
    d = open_action(repo)
    boxes = fig(d)["boxes"]
    send(d, boxes)                                                    # 値を入れ直した直後の change
    assert d.last == {} and "未保存" not in d["status"]
    boxes[0] = box("#1", boxes[0]["xmin"], boxes[0]["ymin"] - 1, boxes[0]["xmax"], boxes[0]["ymax"])   # 読み込み時の 1px のずれ
    send(d, boxes)
    assert d.last == {} and cards(d)[0] == [-380, -640, 320, -260]


def test_draw_in_figure_adds_a_card(repo):
    d = open_action(repo)
    boxes = fig(d)["boxes"] + [box("", 1000, 600, 1400, 900)]
    send(d, boxes)
    assert d["cards_n"] == 4 and cards(d)[3] == [-424, -536, -124, -136]
    assert d["c_label3"] == "object" and d["c_caption3"] == ""
    assert d.update_of("c_card3")["visible"] is True
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#2", "#3", "#4"]   # 描いた四角はカードの番号付きで描き直す
    assert "未保存" in d["status"]


def test_draw_beyond_the_recommended_8_still_adds(repo):
    """おすすめは8件までだが、実際の上限は32件。8件のときに図で描くと9件目が追加される（警告もエラーもない）。"""
    d = open_action(repo)
    while d["cards_n"] < RECOMMENDED_ITEMS:
        d.call("card_add")
    assert d["cards_n"] == 8
    send(d, fig(d)["boxes"] + [box("", 1000, 600, 1400, 900)])
    assert d["cards_n"] == 9 and cards(d)[8] == [-424, -536, -124, -136]
    assert d.update_of("c_card8")["visible"] is True
    assert len(fig(d)["boxes"]) == 9 and d["bbox_fig_msg"] == ""
    d.call("check")
    assert "❌" not in d["issues"]


def test_draw_is_ignored_at_the_max_of_32(repo):
    d = open_action(repo)
    while d["cards_n"] < MAX_ITEMS:
        d.call("card_add")
    assert d["cards_n"] == MAX_ITEMS == 32
    before = cards(d)
    send(d, fig(d)["boxes"] + [box("", 1000, 600, 1400, 900)])
    assert d["cards_n"] == MAX_ITEMS and cards(d) == before         # 図では追加されない
    assert f"最大{MAX_ITEMS}件" in d["bbox_fig_msg"]
    assert len(fig(d)["boxes"]) == MAX_ITEMS                          # 余分な四角は消す


def test_figure_cannot_delete(repo):
    d = open_action(repo)
    before = cards(d)
    send(d, fig(d)["boxes"][1:])                                      # #1 の四角が図から消えた（Delete キーなど）
    assert d["cards_n"] == 3 and cards(d) == before and "未保存" not in d["status"]
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#2", "#3"]   # カードの値で描き直す
    send(d, [])
    assert d["cards_n"] == 3 and cards(d) == before


def test_out_of_range_card_is_not_editable_and_keeps_its_value(repo):
    d = open_action(repo)
    d["c_y01"] = -5000                                                # #2 を、余白を含めた範囲にも収まらない値にする
    d.call("fig_refresh_y01")
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#3"]
    assert "#2 は図の範囲外のため、カードで編集してください" in d["bbox_fig_msg"]
    boxes = fig(d)["boxes"]
    boxes[0] = box("#1", boxes[0]["xmin"] + 10, boxes[0]["ymin"], boxes[0]["xmax"] + 10, boxes[0]["ymax"])
    send(d, boxes)
    assert d["c_y01"] == -5000 and cards(d)[1][1:] == [-190, 320, 190]    # #2 は変わらない
    assert cards(d)[0][1] == -630


def test_card_in_margin_is_editable_and_still_warns(repo, data_dir):
    d = open_action(repo)
    d["c_x11"] = 1000                                                 # キャンバスの外（x_max=1000 > 768）。図の範囲（±1536）には収まる
    d.call("fig_refresh_x11")
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#2", "#3"]
    d.call("check")
    assert "bbox" in d["issues"] and "⚠" in d["issues"] and "❌" not in d["issues"]   # はみ出しは警告（保存はできる）


def test_card_operations_update_the_figure(repo):
    d = open_action(repo)
    d.call("card_del_0")
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#2"]
    d.call("card_up_1")                                               # 並べ替えても番号は位置どおり
    assert [(b["xmin"], b["ymin"]) for b in fig(d)["boxes"]][0] == (1536 + cards(d)[0][1], 1024 + cards(d)[0][0])
    d.call("card_full_0")
    b = fig(d)["boxes"][0]
    assert (b["xmin"], b["ymin"], b["xmax"], b["ymax"]) == (768, 512, 2304, 1536)       # ［全面］ = キャンバスの枠
    d.call("card_add")
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#2", "#3"]


def test_number_fields_update_the_figure_on_blur(repo):
    d = open_action(repo)
    d["c_x00"], d["c_y00"], d["c_x10"], d["c_y10"] = -100, -50, 100, 50
    d.call("fig_refresh_x00")
    b = fig(d)["boxes"][0]
    assert (b["xmin"], b["ymin"], b["xmax"], b["ymax"]) == (1436, 974, 1636, 1074)


def test_preview_background_decides_the_canvas(data_dir):
    write(data_dir / "backgrounds" / "wide_bg.yaml",
          "id: wide_bg\nname: 横長\ncanvas: { w: 2048, h: 1024 }\nmossCaption: x\nanima: x\n"
          "mossItems:\n  - { label: object, caption: ref, bbox: [-100, -200, 100, 200] }\n")
    d = open_action(Repository(data_dir))
    assert fig(d)["image"].size == (3072, 2048)
    d.call("pick_pv_bg")
    d.call("pk_pick", "wide_bg")
    assert fig(d)["image"].size == (4096, 2048)                       # プレビューで選んだ Background の canvas（2048×1024 + 余白）
    assert [b["label"] for b in fig(d)["boxes"]] == ["#1", "#2", "#3"]
    d.call("card_full_0")                                              # ［全面］も同じキャンバス
    assert cards(d)[0] == [-512, -1024, 512, 1024]


def test_background_editing_uses_own_canvas_and_margin(repo, data_dir):
    d = open_item(repo, "backgrounds", "backgrounds/beach.yaml")
    assert fig(d)["image"].size == (3072, 2048) and [b["label"] for b in fig(d)["boxes"]] == ["#1"]
    d["cw"], d["ch"] = 1024, 1024
    d.call("fig_refresh_cw")
    assert fig(d)["image"].size == (2048, 2048)
    boxes = fig(d)["boxes"]
    send(d, boxes + [box("", 600, 600, 900, 800)])
    assert d["cards_n"] == 2 and "未保存" in d["status"]


def test_margin_option_changes_the_base(repo):
    d = Driver(build_editor(repo, bbox_margin=0))
    d.call("init")
    d.call("section", "actions")
    d.call("browse", "file:actions/beach_chat.yaml")
    assert fig(d)["image"].size == (1536, 1024)
    d2 = Driver(build_editor(repo, bbox_margin=1))
    d2.call("init")
    d2.call("section", "actions")
    d2.call("browse", "file:actions/beach_chat.yaml")
    assert fig(d2)["image"].size == (4608, 3072)
    assert BE.make_frame({"w": 1536, "h": 1024}, 1).ox == 2304
