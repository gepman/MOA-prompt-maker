"""画面の組み立て（イベント配線を含む）が例外なく通ること。構文エラーや配線ミスを拾う。"""
from conftest import ROOT
from moa.comfy import load_config
from moa.ui.app import build_ui


def test_ui_builds_without_comfy(repo):
    build_ui(repo)


def test_ui_builds_with_comfy_config(repo, tmp_path):
    cfg_text = (ROOT / "config.yaml").read_text(encoding="utf-8").replace(
        "workflow/moss_to_anima_i2i_api_v1.1.json", str(ROOT / "tests" / "fixtures" / "workflow.json").replace("\\", "/"))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(cfg_text, encoding="utf-8")
    cfg_obj, issues = load_config(cfg)
    assert cfg_obj is not None, issues
    build_ui(repo, lambda: load_config(cfg))
