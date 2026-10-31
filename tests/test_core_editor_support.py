"""エディタを支える moa/core の共通処理：オーバーレイ、フォルダ名、外部変更の検知、書き戻し、設定。"""
import os
from dataclasses import replace

import pytest

from conftest import scene_of, write
from moa.core import Repository, compile_scene, save_scene
from moa.core.appconfig import config_port
from moa.core.entity_io import apply_values, save_entity, save_folder_meta
from moa.core.filestamp import ExternalChangeError, stamp
from moa.core.folders import create_folder, validate_folder_name
from moa.core.overlay import OverlayRepository
from moa.core.repository import load_yaml
from moa.core.usage import scenes_using_action, scenes_using_background
from moa.core.yaml_io import load_roundtrip


# ---- オーバーレイ ----
def test_overlay_replaces_character_and_leaves_files(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    before = path.read_bytes()
    draft = replace(repo.character("elesa_bw"), moss="DRAFT MOSS")
    ov = OverlayRepository(repo, {("character", "elesa_bw"): draft})
    r = compile_scene(ov, repo.scene("beach_chat__trio"))
    assert r.ok and "DRAFT MOSS" in r.moss_prompt
    assert "DRAFT MOSS" not in compile_scene(repo, repo.scene("beach_chat__trio")).moss_prompt
    assert path.read_bytes() == before


def test_overlay_new_id(repo):
    new = replace(repo.action("beach_solo"), id="brand_new")
    ov = OverlayRepository(repo, {("action", "brand_new"): new})
    assert ov.action("brand_new") is new and "brand_new" in ov.ids("action") and repo.action("brand_new") is None


# ---- フォルダ名 ----
@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "_x", ".x", "a?b", "a<b", "a:b", 'a"b', "a|b", "a*b",
                                  "末尾.", "末尾 ", "CON", "con", "con.txt", "NUL", "COM1", "lpt9.log", "a\x01b"])
def test_folder_name_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        validate_folder_name(tmp_path, name)


def test_folder_name_ok_and_duplicate(tmp_path):
    root = tmp_path / "characters"
    root.mkdir()
    p = create_folder(root, root, "ポケモン")
    assert p.is_dir() and not (p / "_folder.yaml").exists()
    with pytest.raises(ValueError):
        create_folder(root, root, "ポケモン")
    with pytest.raises(ValueError):
        create_folder(root, tmp_path, "outside")
    assert validate_folder_name(tmp_path, "COM10") == "COM10"


# ---- 使用中の判定 ----
def test_usage(repo):
    assert [e.id for e in scenes_using_background(repo, "beach")] == [
        "beach_chat__trio", "beach_solo__elesa", "beach_wave_boy__elesa"]
    assert [e.id for e in scenes_using_action(repo, "beach_chat")] == ["beach_chat__trio"]


# ---- 書き戻し ----
def _values_of(repo, kind, eid):
    from moa.editor.logic.forms import normalize, values_of
    return normalize(kind, values_of(repo.entry(kind, eid).obj))


def test_entity_io_changes_only_name(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    before = path.read_text(encoding="utf-8")
    values = _values_of(repo, "character", "elesa_bw") | {"name": "カミツレ改"}
    save_entity(repo, "character", values, path=path)
    after = path.read_text(encoding="utf-8")
    assert after == before.replace("name: カミツレ（BW版）", "name: カミツレ改")


def test_entity_io_removes_empty_optional_and_keeps_required(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    values = _values_of(repo, "character", "elesa_bw") | {
        "defaultOutfit": None, "bodyHairMoss": "", "bodyHairAnima": "  ", "moss": ""}
    save_entity(repo, "character", values, path=path)
    data = load_yaml(path)
    assert "defaultOutfit" not in data and "bodyHairMoss" not in data and "bodyHairAnima" not in data
    assert data["moss"] == ""
    assert "null" not in path.read_text(encoding="utf-8")


def test_entity_io_inserts_in_canonical_position(repo, data_dir):
    path = data_dir / "outfits" / "hex_summer.yaml"
    values = _values_of(repo, "outfit", "hex_summer") | {"negativeMoss": "no hat"}
    save_entity(repo, "outfit", values, path=path)
    keys = list(load_roundtrip(path))
    assert keys.index("negativeMoss") > keys.index("anima")


def test_entity_io_new_file_is_valid_and_loads(repo, data_dir):
    from moa.editor.logic.forms import new_values, normalize
    values = new_values("action") | {"id": "fresh", "name": "新規"}
    path = save_entity(repo, "action", normalize("action", values), folder=data_dir / "actions")
    assert path.name == "fresh.yaml"
    repo.reload()
    a = repo.action("fresh")
    assert a is not None and a.slots == ["main"] and len(a.moss_items) == 1 and a.moss_items[0].bbox == [-512, -768, 512, 768]
    assert not [i for i in repo.issues if i.path == path and i.level == "error"]


def test_entity_io_refuses_existing_new_path_and_outside_root(repo, data_dir):
    values = _values_of(repo, "outfit", "hex_summer")
    with pytest.raises(ValueError):
        save_entity(repo, "outfit", values, folder=data_dir / "outfits")
    with pytest.raises(ValueError):
        save_entity(repo, "outfit", values | {"id": "zzz"}, folder=data_dir / "actions")


def test_folder_meta_creates_and_keeps_empty(tmp_path):
    folder = tmp_path / "characters" / "pokemon"
    folder.mkdir(parents=True)
    path = save_folder_meta(folder, "ポケモン", "説明", ["a.yaml", " ", "b.yaml"])
    assert load_yaml(path) == {"name": "ポケモン", "description": "説明", "order": ["a.yaml", "b.yaml"]}
    save_folder_meta(folder, "", "", [])
    assert path.exists() and load_yaml(path) in ({}, None)


# ---- 外部変更の検知 ----
def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_stamp_changes_on_touch(tmp_path):
    p = write(tmp_path / "x.yaml", "a: 1\n")
    s = stamp(p)
    _bump(p)
    assert stamp(p) != s and stamp(tmp_path / "none.yaml") is None


def test_save_entity_detects_external_change(repo, data_dir):
    path = data_dir / "characters" / "elesa_bw.yaml"
    s = repo.entry("character", "elesa_bw").stamp
    values = _values_of(repo, "character", "elesa_bw")
    _bump(path)
    with pytest.raises(ExternalChangeError):
        save_entity(repo, "character", values, path=path, expected_stamp=s)
    save_entity(repo, "character", values, path=path, expected_stamp=stamp(path))


def test_save_scene_detects_external_change(repo, data_dir):
    scene = repo.scene("beach_chat__trio")
    result = compile_scene(repo, scene)
    path = data_dir / "scenes" / "beach_chat__trio.yaml"
    s = repo.entry("scene", "beach_chat__trio").stamp
    _bump(path)
    with pytest.raises(ExternalChangeError):
        save_scene(repo, scene, result, expected_stamp=s)
    save_scene(repo, scene, result, expected_stamp=stamp(path))


# ---- 設定 ----
def test_config_port(tmp_path):
    cfg = write(tmp_path / "config.yaml", "builder: {port: 7000}\neditor: {port: bad}\n")
    assert config_port(cfg, "builder", 7860) == 7000
    assert config_port(cfg, "editor", 7863) == 7863
    assert config_port(tmp_path / "missing.yaml", "builder", 7860) == 7860
