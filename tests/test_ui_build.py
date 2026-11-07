"""画面の組み立て（イベント配線を含む）が例外なく通ること。構文エラーや配線ミスを拾う。"""
from conftest import ROOT
from moa.comfy import load_config
from moa.ui.app import build_ui


def test_ui_builds_without_comfy(repo):
    build_ui(repo)


def test_export_block_is_wired(repo):
    names = {fn.api_name for fn in build_ui(repo).fns.values()}
    assert {"ex_mode", "ex_move", "export_scene", "export_folder"} <= names          # 書き出しブロック（10.3章）
    assert {"ex_after_browse", "ex_after_upload", "ex_after_reload", "ex_after_overwrite", "ex_after_init"} <= names


def test_ui_builds_with_comfy_config(repo, tmp_path):
    cfg_text = (ROOT / "config.yaml").read_text(encoding="utf-8").replace(
        "workflow/moss_to_anima_i2i_api_v1.1.json", str(ROOT / "tests" / "fixtures" / "workflow.json").replace("\\", "/"))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(cfg_text, encoding="utf-8")
    cfg_obj, issues = load_config(cfg)
    assert cfg_obj is not None, issues
    demo = build_ui(repo, lambda: load_config(cfg))
    names = {fn.api_name for fn in demo.fns.values()}
    assert {"scale_set", "scale_reset", "generate", "retry"} <= names   # 解像度倍率の配線
    assert {"save_overwrite", "save_as", "save_confirm", "save_cancel", "save_ext_overwrite", "ext_reload",
            "sv_move", "sv_select", "mkdir"} <= names                    # 保存ブロックの配線（10.2章）
    assert not {"pick_savedir", "set_savedir"} & names                  # 共通ピッカーでは保存先を選ばない
