"""エディタの画面の組み立て（イベント配線を含む）が例外なく通ること。"""
import gradio as gr

from conftest import check_output_shape
from moa.core.repository import load_yaml
from moa.editor.logic.forms import MAX_ITEMS, RECOMMENDED_ITEMS
from moa.editor.ui.app import build_editor


def test_editor_builds(repo):
    build_editor(repo)


class Driver:
    """Gradio のイベントを、ブラウザなしで api_name から呼ぶ（入力は部品の現在値を持ち回る）。

    ハンドラーの返り値は、Gradio の出力検証（個数・部品の対応）に通す。
    空の dict を返して「出力の個数が足りない」になる不具合を、ここで見逃さないため。
    """

    def __init__(self, demo):
        self.demo = demo
        self.c = demo.named
        self.fns = {f.api_name: f for f in demo.fns.values() if f.api_name}
        self.cur = {id(comp): comp.value for comp in demo.blocks.values() if hasattr(comp, "value")}
        self.last = {}

    def __getitem__(self, name):
        return self.cur[id(self.c[name])]

    def __setitem__(self, name, value):
        self.cur[id(self.c[name])] = value

    def call(self, api, *lead, **kw):
        """lead は先頭の入力（Radio の選択値など）。残りは現在値。kw は gr.Request など、画面の部品ではない引数（request=…）。"""
        fn = self.fns[api]
        args = list(lead) + [self.cur[id(comp)] for comp in fn.inputs[len(lead):]]
        out = fn.fn(*args, **kw)
        check_output_shape(self.demo, fn, out)   # 個数が合わなければここで ValueError
        if out == gr.skip():   # 単独の gr.skip() は、全部の出力を「更新なし」にする
            out = {}
        elif isinstance(out, (list, tuple)):   # リストで返すハンドラーは、outputs の順に部品へ対応づける（skip は更新なし）
            out = {comp: upd for comp, upd in zip(fn.outputs, out) if upd != gr.skip()}
        self.last = {}
        for comp, upd in (out or {}).items():
            self.last[id(comp)] = upd
            if isinstance(upd, dict) and upd.get("__type__") == "update":
                if "value" in upd:
                    self.cur[id(comp)] = upd["value"]
            else:
                self.cur[id(comp)] = upd
        return out

    def update_of(self, name):
        return self.last.get(id(self.c[name]))


def test_editor_flow_edit_save_and_navigate(repo, data_dir):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/elesa_bw.yaml")
    assert d["id"] == "elesa_bw" and d["st"]["mode"] == "edit"
    d["name"] = "カミツレ改"
    d.call("save")
    assert "保存しました" in d["save_msg"]
    assert "name: カミツレ改" in (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")

    d["name"] = "もう一度"                                  # 未保存のまま別のルートへ → 止まる
    d.call("section", "outfits")
    assert d["st"]["section"] == "characters" and d["st"]["pending"]
    d.call("discard")
    assert d["st"]["section"] == "outfits" and d["st"]["mode"] is None


def test_editor_flow_background_copy_on_write(repo, data_dir):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("section", "backgrounds")
    d.call("browse", "file:backgrounds/beach.yaml")
    d["mossCaption"] = "A stormy beach."
    d.call("save")
    assert d.update_of("conf_ok") is not None and d["cow_id"] == "beach_copy"
    assert "stormy" not in (data_dir / "backgrounds" / "beach.yaml").read_text(encoding="utf-8")
    d.call("confirm")
    assert "stormy" in (data_dir / "backgrounds" / "beach_copy.yaml").read_text(encoding="utf-8")
    assert d["st"]["id"] == "beach_copy"


def test_editor_flow_cards_and_preview(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("section", "actions")
    d.call("browse", "file:actions/beach_chat.yaml")
    n0 = d["cards_n"]
    while d["cards_n"] < RECOMMENDED_ITEMS:        # おすすめの8件までは普通に増やせる（ボタンは有効のまま）
        d.call("card_add")
    assert d.update_of("add_btn")["interactive"] is True and d["add_hint"] == ""
    while d["cards_n"] < MAX_ITEMS:                 # 実際の上限は64件
        d.call("card_add")
    assert d.update_of("add_btn")["interactive"] is False and d["add_hint"] == f"最大{MAX_ITEMS}件です"
    d.call("card_full_0")
    assert [d[f"c_{k}0"] for k in ("y0", "x0", "y1", "x1")] == [-512, -768, 512, 768]
    d.call("preview")
    assert d["pv_moss"] and "補完" in d["pv_notes"] and n0 >= 1


def test_editor_flow_new_and_folder(repo, data_dir):
    d = Driver(build_editor(repo))
    d.call("init")
    d["folder_tb"] = "CON"
    d.call("mkdir", "CON", d["st"])
    assert d["nav_msg"].startswith("⚠")
    d.call("mkdir", "ポケモン", d["st"])
    assert (data_dir / "characters" / "ポケモン").is_dir()
    d.call("new")
    assert d["st"]["mode"] == "new"
    d["id"], d["name"], d["moss"], d["anima"] = "fresh_char", "新キャラ", "m", "a"
    d.call("save")
    assert (data_dir / "characters" / "fresh_char.yaml").exists() and d["st"]["mode"] == "edit"


def test_editor_flow_folder_meta_check_preview_and_save(repo, data_dir):
    """フォルダ（_folder.yaml）の編集：検証・プレビュー・保存が例外なく動く。

    以前は、フォルダモードの［検証］が空の dict を返し、「output values（needed: 257, returned: 1）」で落ちた。
    """
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("mkdir", "ポケモン", d["st"])
    d.call("browse", "dir:characters/ポケモン")
    assert d["st"]["mode"] == "folder"
    d["f_name"], d["f_order"] = "ポケモン", "nothing.yaml"

    d["issues"] = "- ❌ 前の項目のエラー"
    d.call("check")
    assert d["issues"] == ""                                         # フォルダの検証では、前の項目のエラー表示が残らない
    d.call("preview")                                                # フォルダにプレビューはない（何も変えずに終わる）
    d.call("save")
    assert "保存しました" in d["save_msg"] and "nothing.yaml" in d["issues"]   # order の警告は保存の結果に出る
    assert load_yaml(data_dir / "characters" / "ポケモン" / "_folder.yaml")["name"] == "ポケモン"
    d.call("check")                                                  # 保存の後に、もう一度検証しても落ちない
    assert d["issues"] == ""


def test_editor_events_with_nothing_to_update_do_not_raise(repo):
    """何も開いていない・ピッカーも閉じている状態で押せるイベントが、更新なしで終わる（gr.skip()）。"""
    d = Driver(build_editor(repo))
    d.call("init")
    for api in ("check", "preview", "pv_use", "pk_none", "pk_search", "pk_clear_query"):
        d.call(api)
    d.call("browse", None)
    d.call("pk_dir", None)
    d.call("pk_pick", None)


def _summer_outfits(data_dir):
    from conftest import write
    write(data_dir / "outfits" / "summer" / "_folder.yaml", 'name: "夏服"\n')
    write(data_dir / "outfits" / "summer" / "aa_first.yaml", 'id: aa_first\nname: "ビキニ"\nmoss: m\nanima: a\n')


def test_picker_sets_the_default_outfit_of_a_character(repo, data_dir):
    _summer_outfits(data_dir)
    repo.reload()
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/elesa_bw.yaml")
    d.call("pick_def")
    assert d.update_of("pk_bar")["open"] is True and "defaultOutfit" in d["pk_target"]
    d.call("pk_dir", "outfits/summer")                                   # サブフォルダへ
    assert d["pk_msg"].startswith("1 件") and d.update_of("pk_items")["choices"] == [("ビキニ（aa_first）", "aa_first")]
    d.call("pk_pick", "aa_first")
    assert d["def_pick"] == "aa_first" and d["def_mode"] == "outfit" and "ビキニ" in d["def_pick_show"]
    assert d.update_of("pk_bar")["open"] is False and "未保存" in d["status"]
    d.call("save")
    assert "defaultOutfit: aa_first" in (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")

    d.call("pick_def")                                                   # 選択中の項目のフォルダで開く
    assert d["pk"]["cwd"] == "outfits/summer"
    d.call("pk_cancel")
    assert d.update_of("pk_bar")["open"] is False and d["def_pick"] == "aa_first"


def test_picker_search_in_the_sidebar(repo, data_dir):
    _summer_outfits(data_dir)
    repo.reload()
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/elesa_bw.yaml")
    d.call("pick_def")
    d.call("pk_search", "ビキニ")
    assert d.update_of("pk_items")["choices"] == [("ビキニ（aa_first）　📁 outfits / 夏服", "aa_first")]
    d.call("pk_clear_query")
    assert d["pk"]["query"] == "" and d.update_of("pk_dirs")["choices"] == [("夏服", "outfits/summer")]


def test_picker_for_action_background_slot_outfits_and_preview(repo):
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("section", "actions")
    d.call("browse", "file:actions/beach_chat.yaml")
    slot0 = d["slots"].split(",")[0].strip()

    d.call("pick_act_bg")
    d.call("pk_pick", "white_background")
    assert d["act_bg"] == "white_background" and d.update_of("bbox_fig") is not None
    d.call("pick_act_bg")
    assert d["pk"]["cwd"] == "backgrounds" and d.update_of("pk_none")["visible"] is True   # 空にできる欄
    d.call("pk_none")
    assert d["act_bg"] == "" and "未設定" in d["act_bg_show"]

    d.call("pick_o0")
    assert f"outfits.{slot0}" in d["pk_target"] and d.update_of("pk_none")["visible"] is False
    d.call("pk_pick", "elesa_sling")
    assert d["o_pick0"] == "elesa_sling" and d["o_mode0"] == "outfit"

    d.call("pick_pv_bg")
    d.call("pk_pick", "white_background")
    assert d["pv_bg"] == "white_background" and "white_background" in d["pv_bg_show"]
    d.call("preview")
    assert d["pv_bg"] == "white_background" and d["pv_moss"]


def test_picker_gives_the_whole_width_to_whichever_list_is_not_empty(repo, data_dir):
    from conftest import write
    write(data_dir / "outfits" / "only_dirs" / "inner" / "x.yaml", 'id: x\nname: "x"\nmoss: m\nanima: a\n')
    repo.reload()
    d = Driver(build_editor(repo))
    d.call("init")
    d.call("browse", "file:characters/elesa_bw.yaml")
    d.call("pick_def")
    assert d.update_of("pk_dirs")["visible"] is True and d.update_of("pk_items")["visible"] is True   # 両方ある
    d.call("pk_dir", "outfits/only_dirs")                                                            # フォルダだけ
    assert d.update_of("pk_dirs")["visible"] is True and d.update_of("pk_items")["visible"] is False
    d.call("pk_dir", "outfits/only_dirs/inner")                                                      # 項目だけ
    assert d.update_of("pk_dirs")["visible"] is False and d.update_of("pk_items")["visible"] is True
