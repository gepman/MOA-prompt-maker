"""インポートの画面（EDITOR_SPEC 15章）。Gradio のイベントをブラウザなしで呼ぶ（tests/test_editor_build.py の Driver）。"""
import zipfile
from types import SimpleNamespace

import pytest

from conftest import write
from moa.editor.ui.app import build_editor
from test_editor_build import Driver

CHAR = "id: newchar\nname: 新キャラ\nmoss: m\nanima: a\n"
BG = 'id: newbg\nname: 新背景\ncanvas: {w: 1536, h: 1024}\nmossCaption: ""\nanima: ""\n'


def make_zip(tmp_path, files: dict, name="pack.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, content in files.items():
            zf.writestr(member, content)
    return path


def req(host):
    return SimpleNamespace(client=SimpleNamespace(host=host))


@pytest.fixture
def d(repo):
    driver = Driver(build_editor(repo))
    driver.call("init")
    return driver


def test_upload_inspect_and_import(d, data_dir, tmp_path):
    src = make_zip(tmp_path, {"backgrounds/newbg.yaml": BG, "characters/a/newchar.yaml": CHAR, "readme.txt": "x"})
    d.call("imp_file", str(src))
    assert d["imp_source"] == {"kind": "upload", "path": str(src)}
    d.call("imp_inspect")
    assert "取り込む予定：2 件" in d["imp_summary"] and "無視 1 件" in d["imp_summary"]
    assert d.update_of("imp_box")["visible"] is True and d["imp_insp"] is not None
    assert not (data_dir / "backgrounds" / "newbg.yaml").exists()                         # 検査では何も書かない

    d.call("imp_apply")
    assert (data_dir / "backgrounds" / "newbg.yaml").is_file() and (data_dir / "characters" / "a" / "newchar.yaml").is_file()
    results = {r[0]: r[1] for r in d["imp_result_df"]}
    assert results["backgrounds/newbg.yaml"].startswith("取り込んだ（新規）") and results["readme.txt"] == "無視"
    assert d.update_of("imp_box")["visible"] is False and d["imp_insp"] is None            # 取り込んだら確認画面は閉じる
    assert "上書きしていません" in d["imp_msg"]
    assert ("📁 a", "dir:characters/a") in d.update_of("lst")["choices"]                  # ツリーも更新される


def test_a_conflict_is_edited_in_the_table_and_imported_with_a_new_id(d, data_dir, tmp_path):
    original = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")
    changed = original.replace("カミツレ（BW版）", "別物", 1)
    d.call("imp_file", str(make_zip(tmp_path, {"characters/elesa_bw.yaml": changed})))
    d.call("imp_inspect")
    assert d.update_of("imp_conf_df")["visible"] is True
    rows = d["imp_conf_df"]
    assert rows[0][4] is False and rows[0][5] == "elesa_bw_copy"                          # 既定はスキップ。新 ID の初期値は _copy
    assert "スキップ 1 件" in d["imp_summary"]

    rows[0][4] = True                                                                       # 「別IDで取り込む」にチェック
    d["imp_conf_df"] = rows
    d.call("imp_refresh")
    assert "別ID 1 件" in d["imp_summary"] and "確認を更新しました" in d["imp_msg"]
    d.call("imp_apply")
    assert (data_dir / "characters" / "elesa_bw_copy.yaml").read_text(encoding="utf-8") == changed.replace(
        "id: elesa_bw\n", "id: elesa_bw_copy\n", 1)
    assert (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8") == original


def test_a_kind_is_chosen_in_the_table_for_a_file_that_fits_two_kinds(d, data_dir, tmp_path):
    src = write(tmp_path / "up" / "x.yaml", CHAR)
    d.call("imp_file", str(src))
    d.call("imp_inspect")
    assert d.update_of("imp_kinds_df")["visible"] is True and "種類が未選択 1" in d["imp_summary"]
    rows = d["imp_kinds_df"]
    assert rows[0][1] == "character / outfit" and rows[0][2] == ""
    rows[0][2] = "outfit"
    d["imp_kinds_df"] = rows
    d.call("imp_apply")
    assert (data_dir / "outfits" / "newchar.yaml").is_file() and not (data_dir / "characters" / "newchar.yaml").exists()


def test_the_destination_per_kind_is_chosen_with_the_common_picker_in_folder_mode(d, data_dir, tmp_path):
    (data_dir / "backgrounds" / "sub").mkdir()
    d.call("imp_file", str(make_zip(tmp_path, {"pack/newbg.yaml": BG})))
    d.call("imp_inspect")
    assert d.update_of("imp_dest_row_background")["visible"] is True                          # 配置先を選ぶ種類だけ出す
    assert d.update_of("imp_dest_row_character")["visible"] is False
    assert "backgrounds/（直下）" in d["imp_dest_show_background"]

    d.call("imp_pick_background")
    assert d["pk"]["target"] == "imp_background" and "取り込み先のフォルダ（背景）" in d["pk_target"]
    assert d.update_of("pk_here")["visible"] is True and d.update_of("pk_items")["visible"] is False   # 項目は出さず、フォルダを選ばせる
    d.call("pk_dir", "backgrounds/sub")
    assert d["pk"]["cwd"] == "backgrounds/sub"
    d.call("imp_pk_here")
    assert d["imp_dest_background"] == "backgrounds/sub" and "backgrounds/sub" in d["imp_dest_show_background"]
    assert d.update_of("pk_bar")["open"] is False and d.update_of("pk_here")["visible"] is False   # 選んだら閉じる

    d.call("imp_refresh")
    d.call("imp_apply")
    assert (data_dir / "backgrounds" / "sub" / "pack" / "newbg.yaml").is_file()               # 配置先の下に元の構成を保つ


def test_the_picker_still_picks_entities_after_being_used_for_folders(d, data_dir):
    d.call("browse", "file:characters/elesa_bw.yaml")
    d.call("pick_def")
    assert d.update_of("pk_here")["visible"] is False and d.update_of("pk_items")["visible"] is True


def test_apply_without_an_inspection_and_cancel(d, data_dir, tmp_path):
    d.call("imp_apply")
    assert "先に［検査］" in d["imp_msg"]
    d.call("imp_inspect")
    assert "アップロードするか" in d["imp_msg"]
    d.call("imp_file", str(make_zip(tmp_path, {"backgrounds/newbg.yaml": BG})))
    d.call("imp_inspect")
    d.call("imp_cancel")
    assert d.update_of("imp_box")["visible"] is False and d["imp_insp"] is None and "キャンセル" in d["imp_msg"]
    assert not (data_dir / "backgrounds" / "newbg.yaml").exists()


def test_a_file_saved_while_the_confirmation_is_open_is_not_overwritten(d, data_dir, tmp_path):
    d.call("imp_file", str(make_zip(tmp_path, {"backgrounds/newbg.yaml": BG})))
    d.call("imp_inspect")
    mine = write(data_dir / "backgrounds" / "newbg.yaml",
                 "id: newbg\nname: 先に保存された\ncanvas: {w: 1, h: 1}\nmossCaption: ''\nanima: ''\n")
    d.call("imp_apply")
    assert "先に保存された" in mine.read_text(encoding="utf-8")
    assert d["imp_result_df"][0][1] != "取り込んだ（新規）" and "取り込んだ：0 件" in d["imp_summary"]


# ---- PC 以外からの接続（15.1章）----
def test_the_folder_button_is_disabled_for_remote_connections_with_a_reason(d):
    d.call("imp_load", request=req("192.168.1.9"))
    assert d.update_of("imp_dir_btn")["interactive"] is False
    assert "PC 以外" in d["imp_dir_note"] and "アップロードは使えます" in d["imp_dir_note"].replace("zip・yaml の", "")
    d.call("imp_load", request=req("127.0.0.1"))
    assert d.update_of("imp_dir_btn")["interactive"] is True and d["imp_dir_note"] == ""
    d.call("imp_load", request=None)                                                             # 接続元を確認できないときも無効
    assert d.update_of("imp_dir_btn")["interactive"] is False


def test_the_folder_handler_refuses_remote_connections_without_opening_the_dialog(d, monkeypatch):
    def never(*a, **k):
        raise AssertionError("ダイアログを開いてはいけない")

    monkeypatch.setattr("moa.editor.ui.import_ui.pick_folder", never)
    for request in (req("10.0.0.2"), None):
        d.call("imp_dir", request=request)
        assert "PC 以外" in d["imp_dir_note"] and d["imp_source"] is None and not d["imp_dir_show"]


def test_the_folder_dialog_result_is_shown_read_only_and_used_as_the_source(d, data_dir, tmp_path, monkeypatch):
    root = tmp_path / "mydata"
    write(root / "backgrounds" / "newbg.yaml", BG)
    asked = {}

    def fake_pick(initial="", title=""):
        asked["title"] = title
        return str(root)

    monkeypatch.setattr("moa.editor.ui.import_ui.pick_folder", fake_pick)
    d.call("imp_dir", request=req("127.0.0.1"))
    assert d["imp_source"] == {"kind": "folder", "path": str(root)} and d["imp_dir_show"] == str(root)
    assert "取り込む" in asked["title"]
    d.call("imp_inspect")
    d.call("imp_apply")
    assert (data_dir / "backgrounds" / "newbg.yaml").is_file()

    monkeypatch.setattr("moa.editor.ui.import_ui.pick_folder", lambda *a, **k: None)           # キャンセルしても何も変わらない
    d.call("imp_dir", request=req("127.0.0.1"))
    assert d["imp_source"] == {"kind": "folder", "path": str(root)}


def test_a_dialog_failure_is_reported_in_the_note(d, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("フォルダ選択ダイアログを開けませんでした")

    monkeypatch.setattr("moa.editor.ui.import_ui.pick_folder", broken)
    d.call("imp_dir", request=req("127.0.0.1"))
    assert "開けませんでした" in d["imp_dir_note"]


def test_there_is_no_path_text_input_and_the_shown_path_is_read_only(repo):
    demo = build_editor(repo)
    assert demo.named["imp_dir_show"].interactive is False
    texts = [n for n, comp in demo.named.items() if n.startswith("imp_") and type(comp).__name__ == "Textbox"]
    assert texts == ["imp_dir_show"]                                                           # パスの手入力欄は作らない
