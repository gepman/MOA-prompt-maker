"""`_folder.yaml` の共通処理（moa.core.foldermeta）：検証つきの保存・外部変更の検知・コメントの保持。ビルダーとエディタが共有する。"""
import subprocess
import sys

from conftest import ROOT, write
from moa.core import Repository
from moa.core import foldermeta as FM
from moa.core.repository import load_yaml


def save(repo, folder, name="", description="", order=(), **kw):
    return FM.write_folder_meta(repo, folder, name, description, list(order), **kw)


def test_saves_name_description_and_order_and_reload_matches(repo, data_dir):
    scenes = data_dir / "scenes"
    res = save(repo, scenes, "海辺", "説明です", ["beach_chat__trio.yaml", "beach_solo__elesa.yaml"])
    assert res.status == FM.SAVED and res.path == scenes / "_folder.yaml"
    repo2 = Repository(data_dir)
    meta = repo2.folder_meta[scenes.resolve()]
    assert (meta.name, meta.description, meta.order) == ("海辺", "説明です", ["beach_chat__trio.yaml", "beach_solo__elesa.yaml"])
    assert repo.folder_meta[scenes.resolve()].name == "海辺"       # 保存後に repo は読み直される


def test_creates_the_file_for_a_folder_that_has_none_including_the_scenes_root(repo, data_dir):
    sub = data_dir / "scenes" / "trip"
    sub.mkdir()
    assert not (sub / "_folder.yaml").exists() and not (data_dir / "scenes" / "_folder.yaml").exists()
    assert save(repo, sub, "旅行").status == FM.SAVED and load_yaml(sub / "_folder.yaml") == {"name": "旅行"}
    assert save(repo, data_dir / "scenes", "全部").status == FM.SAVED
    assert load_yaml(data_dir / "scenes" / "_folder.yaml") == {"name": "全部"}


def test_all_empty_does_not_delete_the_file_and_leaves_an_empty_map(repo, data_dir):
    sub = data_dir / "scenes"
    save(repo, sub, "x", "y", ["a.yaml"])
    assert save(repo, sub, "", "", [], expected_stamp=FM.stamp(sub / "_folder.yaml")).status == FM.SAVED
    assert (sub / "_folder.yaml").is_file() and load_yaml(sub / "_folder.yaml") == {}


def test_order_key_is_removed_when_empty_and_comments_stay(repo, data_dir):
    sub = data_dir / "scenes"
    path = write(sub / "_folder.yaml", "# 目次\nname: 旧名   # 表示名\norder:\n  - beach_chat__trio.yaml\n")
    st = FM.stamp(path)
    assert save(repo, sub, "旧名", "", [], expected_stamp=st).status == FM.SAVED
    text = path.read_text(encoding="utf-8")
    assert "order" not in text and "# 目次" in text and "# 表示名" in text and load_yaml(path) == {"name": "旧名"}


def test_untouched_items_keep_their_text_when_only_one_item_changes(repo, data_dir):
    sub = data_dir / "scenes"
    path = write(sub / "_folder.yaml", "# 目次\nname: 旧名   # 表示名\ndescription: 説明\norder:\n  - a.yaml   # 先頭\n  - b.yaml\n")
    assert save(repo, sub, "新名", "説明", ["a.yaml", "b.yaml"], expected_stamp=FM.stamp(path)).status == FM.SAVED
    text = path.read_text(encoding="utf-8")
    assert "# 目次" in text and "# 先頭" in text and "新名" in text          # order は変えていないので項目コメントも残る
    assert load_yaml(path)["order"] == ["a.yaml", "b.yaml"] and load_yaml(path)["description"] == "説明"


def test_external_change_stops_the_save_unless_forced(repo, data_dir):
    sub = data_dir / "scenes"
    path = write(sub / "_folder.yaml", "name: 元\n")
    st = FM.stamp(path)
    write(path, "name: 外で変更した内容\n")                                  # 読み込み後に外部で変更
    res = save(repo, sub, "自分の編集", expected_stamp=st)
    assert res.status == FM.EXTERNAL and load_yaml(path) == {"name": "外で変更した内容"}   # 書かない
    assert save(repo, sub, "自分の編集", expected_stamp=st, force=True).status == FM.SAVED
    assert load_yaml(path) == {"name": "自分の編集"}


def test_a_file_that_appeared_after_loading_counts_as_an_external_change(repo, data_dir):
    sub = data_dir / "scenes"
    write(sub / "_folder.yaml", "name: 後から作られた\n")
    assert save(repo, sub, "x", expected_stamp=None).status == FM.EXTERNAL


def test_schema_violation_refuses_to_save_and_gives_reasons(repo, data_dir):
    sub = data_dir / "scenes"
    path = write(sub / "_folder.yaml", "name: 旧\nunknown: 1\n")           # folder スキーマは未知のキーを許さない
    before = path.read_bytes()
    res = save(repo, sub, "新", expected_stamp=FM.stamp(path))
    assert res.status == FM.ERRORS and "スキーマ" in res.message and res.issues and path.read_bytes() == before


def test_a_broken_yaml_is_not_overwritten(repo, data_dir):
    sub = data_dir / "scenes"
    path = write(sub / "_folder.yaml", "name: [壊れた\n")
    before = path.read_bytes()
    res = save(repo, sub, "新", expected_stamp=FM.stamp(path))
    assert res.status == FM.ERRORS and path.read_bytes() == before


def test_form_values_and_dirty_judgement(repo, data_dir):
    sub = data_dir / "scenes"
    write(sub / "_folder.yaml", "name: 名\norder:\n  - a.yaml\n  - b.yaml\n")
    repo.reload()
    v = FM.folder_values(repo, sub)
    assert v == {"name": "名", "description": "", "order": "a.yaml\nb.yaml"}
    assert not FM.folder_dirty(v | {"order": " a.yaml \n\nb.yaml"}, v) and FM.folder_dirty(v | {"name": "別"}, v)


# ---- 共通処理を使っていること ----
def test_editor_and_builder_share_the_core_implementation():
    import moa.editor.logic.save as editor_save
    import moa.editor.logic.session as editor_session
    import moa.ui.folderedit as builder
    assert editor_save.write_folder_meta is FM.write_folder_meta and builder.write_folder_meta is FM.write_folder_meta
    assert editor_session.folder_values is FM.folder_values and editor_session.folder_dirty is FM.folder_dirty


def test_the_builder_does_not_import_the_editor():
    code = "import sys, moa.ui.app; print(any(m == 'moa.editor' or m.startswith('moa.editor.') for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
    assert out.stdout.strip() == "False"
