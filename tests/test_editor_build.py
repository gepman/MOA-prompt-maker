"""エディタの画面の組み立て（イベント配線を含む）が例外なく通ること。"""
from moa.editor.ui.app import build_editor


def test_editor_builds(repo):
    build_editor(repo)


class Driver:
    """Gradio のイベントを、ブラウザなしで api_name から呼ぶ（入力は部品の現在値を持ち回る）。"""

    def __init__(self, demo):
        self.c = demo.named
        self.fns = {f.api_name: f for f in demo.fns.values() if f.api_name}
        self.cur = {id(comp): comp.value for comp in demo.blocks.values() if hasattr(comp, "value")}
        self.last = {}

    def __getitem__(self, name):
        return self.cur[id(self.c[name])]

    def __setitem__(self, name, value):
        self.cur[id(self.c[name])] = value

    def call(self, api, *lead):
        """lead は先頭の入力（Radio の選択値など）。残りは現在値。"""
        fn = self.fns[api]
        args = list(lead) + [self.cur[id(comp)] for comp in fn.inputs[len(lead):]]
        out = fn.fn(*args)
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
    while d["cards_n"] < 8:
        d.call("card_add")
    assert d.update_of("add_btn")["interactive"] is False and d["add_hint"] == "最大8件です"
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
