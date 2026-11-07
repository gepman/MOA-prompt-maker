"""ビルダーの scenes/ フォルダ情報の編集（SPEC 10.4章）：order の一覧（☑／☐）・並び替え・見つからない項目・保存・保存後の更新。"""
import shutil

from conftest import write
from moa.core.export import export_folder
from moa.core.repository import load_yaml
from moa.core.tree import folder_label, list_folder
from moa.ui import exportblock as EB
from moa.ui import folderedit as FE
from moa.ui import saveblock as SB

SCENES = ["beach_chat__trio.yaml", "beach_solo__elesa.yaml", "beach_wave_boy__elesa.yaml", "guilty_lineup__trio.yaml"]


def names(fe):
    return [r["name"] for r in fe["rows"]]


def checks(fe):
    return {r["name"]: r["included"] for r in fe["rows"]}


def open_(repo, folder):
    return FE.open_folder(repo, folder)


# ---- 一覧 ----
def test_listed_items_come_first_checked_and_unlisted_real_items_follow_unchecked(repo, data_dir):
    scenes = data_dir / "scenes"
    (scenes / "sub").mkdir()
    write(scenes / "_folder.yaml", "order:\n  - guilty_lineup__trio.yaml\n  - beach_chat__trio.yaml\n")
    repo.reload()
    fe = open_(repo, scenes)
    assert names(fe) == ["guilty_lineup__trio.yaml", "beach_chat__trio.yaml",          # order の順
                         "sub", "beach_solo__elesa.yaml", "beach_wave_boy__elesa.yaml"]  # 後ろ：サブフォルダ → ファイル（自然順）
    assert [r["included"] for r in fe["rows"]] == [True, True, False, False, False]
    assert {r["name"]: r["kind"] for r in fe["rows"]}["sub"] == "dir"
    assert FE.labels(fe)[0][0].startswith("☑ 1.") and FE.labels(fe)[2][0].startswith("☐")


def test_a_folder_without_folder_yaml_lists_everything_unchecked_and_nothing_is_added_to_order(repo, data_dir):
    fe = open_(repo, data_dir / "scenes")
    assert sorted(names(fe)) == sorted(SCENES) and not any(checks(fe).values())
    assert FE.order_to_write(fe) == []


def test_hidden_folders_and_the_folder_file_are_not_listed_but_unreadable_yaml_is(repo, data_dir):
    scenes = data_dir / "scenes"
    (scenes / "_hidden").mkdir()
    write(scenes / "broken.yaml", "id: [壊れた\n")
    write(scenes / "_folder.yaml", "name: x\n")
    repo.reload()
    assert "_hidden" not in names(open_(repo, scenes)) and "_folder.yaml" not in names(open_(repo, scenes))
    assert "broken.yaml" in names(open_(repo, scenes))      # 読み込めなくても実在として一覧に出る


# ---- 保存：☑ の項目だけが、表示順で order に入る ----
def test_only_checked_items_are_written_in_display_order_and_export_targets_do_not_grow(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "order:\n  - beach_chat__trio.yaml\n")
    repo.reload()
    assert len(export_folder(data_dir, scenes).scenes) == 1
    fe = open_(repo, scenes)
    fe, _ = FE.toggle(FE.select(fe, "guilty_lineup__trio.yaml"))      # ☐ → ☑
    fe, _ = FE.move(fe, "up")                                           # 表示上で1つ上へ
    out, msg, external = FE.save(repo, fe, "", "")
    assert msg.startswith("💾") and not external
    order = load_yaml(scenes / "_folder.yaml")["order"]
    assert set(order) == {"beach_chat__trio.yaml", "guilty_lineup__trio.yaml"}
    assert "beach_solo__elesa.yaml" not in order and "beach_wave_boy__elesa.yaml" not in order   # ☐ は入らない
    assert len(export_folder(data_dir, scenes).scenes) == 2            # 対象は ☑ にした分だけ増える


def test_reordering_is_reflected_in_order(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "order:\n  - beach_chat__trio.yaml\n  - beach_solo__elesa.yaml\n  - guilty_lineup__trio.yaml\n")
    repo.reload()
    fe = FE.select(open_(repo, scenes), "guilty_lineup__trio.yaml")
    fe, _ = FE.move(fe, "up")
    fe, _ = FE.move(fe, "up")
    assert FE.order_to_write(fe)[:3] == ["guilty_lineup__trio.yaml", "beach_chat__trio.yaml", "beach_solo__elesa.yaml"]
    FE.save(repo, fe, "", "")
    assert load_yaml(scenes / "_folder.yaml")["order"] == ["guilty_lineup__trio.yaml", "beach_chat__trio.yaml", "beach_solo__elesa.yaml"]
    top, msg = FE.move(FE.select(open_(repo, scenes), "guilty_lineup__trio.yaml"), "up")     # 端では何もしない
    assert msg == "" and names(top)[0] == "guilty_lineup__trio.yaml"


def test_unchecking_everything_removes_the_order_key(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "name: 名\norder:\n  - beach_chat__trio.yaml\n")
    repo.reload()
    fe, _ = FE.toggle(FE.select(open_(repo, scenes), "beach_chat__trio.yaml"))
    FE.save(repo, fe, "名", "")
    assert load_yaml(scenes / "_folder.yaml") == {"name": "名"}


def test_missing_names_are_shown_announced_before_saving_and_removed_on_save(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "order:\n  - ghost.yaml\n  - beach_chat__trio.yaml\n")
    repo.reload()
    fe = open_(repo, scenes)
    assert FE.removed_names(fe) == ["ghost.yaml"] and "ghost.yaml" in FE.removed_text(fe)       # 保存前に表示
    assert any("見つからない" in label for label, _ in FE.labels(fe))
    assert FE.toggle(FE.select(fe, "ghost.yaml"))[1].startswith("⚠")                              # 切り替えられない
    out, msg, _ = FE.save(repo, fe, "", "")
    assert load_yaml(scenes / "_folder.yaml")["order"] == ["beach_chat__trio.yaml"] and "ghost.yaml" in msg
    assert FE.removed_names(out) == []


def test_duplicate_names_in_order_are_merged(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "order:\n  - beach_chat__trio.yaml\n  - beach_chat__trio.yaml\n")
    repo.reload()
    assert names(open_(repo, scenes)).count("beach_chat__trio.yaml") == 1


# ---- 新規作成・コメント ----
def test_creates_folder_yaml_for_a_subfolder_and_for_the_scenes_root(repo, data_dir):
    sub = data_dir / "scenes" / "trip"
    sub.mkdir()
    shutil.move(str(data_dir / "scenes" / SCENES[0]), sub / SCENES[0])
    repo.reload()
    fe = FE.toggle(FE.select(open_(repo, sub), SCENES[0]))[0]
    assert FE.save(repo, fe, "旅行", "説明")[1].startswith("💾")
    assert load_yaml(sub / "_folder.yaml") == {"name": "旅行", "description": "説明", "order": [SCENES[0]]}
    fe = FE.toggle(FE.select(open_(repo, data_dir / "scenes"), "trip"))[0]
    assert FE.save(repo, fe, "", "")[1].startswith("💾") and load_yaml(data_dir / "scenes" / "_folder.yaml") == {"order": ["trip"]}


def test_comments_survive_a_save_that_only_changes_the_name(repo, data_dir):
    scenes = data_dir / "scenes"
    path = write(scenes / "_folder.yaml", "# 目次\nname: 旧名   # 表示名\norder:\n  - beach_chat__trio.yaml   # 先頭\n")
    repo.reload()
    FE.save(repo, open_(repo, scenes), "新名", "")
    text = path.read_text(encoding="utf-8")
    assert "# 目次" in text and "# 表示名" in text and "# 先頭" in text and load_yaml(path)["name"] == "新名"


# ---- 外部変更・スキーマ ----
def test_external_change_is_detected_and_can_be_overwritten(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "name: 元\n")
    repo.reload()
    fe = open_(repo, scenes)
    write(scenes / "_folder.yaml", "name: 外で変更した内容\n")
    same, msg, external = FE.save(repo, fe, "自分の編集", "")
    assert external and msg == FE.EXTERNAL_MSG and same is fe and load_yaml(scenes / "_folder.yaml")["name"] == "外で変更した内容"
    out, msg, external = FE.save(repo, fe, "自分の編集", "", force=True)
    assert msg.startswith("💾") and not external and load_yaml(scenes / "_folder.yaml")["name"] == "自分の編集"


def test_schema_violation_refuses_and_shows_the_reason(repo, data_dir):
    scenes = data_dir / "scenes"
    path = write(scenes / "_folder.yaml", "name: 旧\nunknown: 1\n")
    repo.reload()
    before = path.read_bytes()
    _, msg, external = FE.save(repo, open_(repo, scenes), "新", "")
    assert msg.startswith("❌") and "スキーマ" in msg and not external and path.read_bytes() == before


def test_dirty_is_detected_only_for_real_changes(repo, data_dir):
    scenes = data_dir / "scenes"
    write(scenes / "_folder.yaml", "name: 名\norder:\n  - beach_chat__trio.yaml\n")
    repo.reload()
    fe = open_(repo, scenes)
    assert not FE.dirty(fe, "名", "") and not FE.dirty(fe, " 名 ", "")
    assert FE.dirty(fe, "別", "") and FE.dirty(FE.toggle(FE.select(fe, SCENES[1]))[0], "名", "")


# ---- 保存後に、その場で更新される ----
def test_after_saving_the_picker_and_the_export_target_count_are_updated(repo, data_dir):
    scenes = data_dir / "scenes"
    sub = scenes / "trip"
    sub.mkdir()
    shutil.move(str(scenes / SCENES[0]), sub / SCENES[0])
    shutil.move(str(scenes / SCENES[1]), sub / SCENES[1])
    repo.reload()
    ex = {**EB.new_ex(), "cwd": "scenes/trip"}
    assert "0 件" in EB.folder_view(repo, ex)[2]
    fe = open_(repo, sub)
    for n in (SCENES[1], SCENES[0]):                                   # 2件を ☑ にする（SCENES[0] が先頭になるよう並べる）
        fe, _ = FE.toggle(FE.select(fe, n))
    out, msg, _ = FE.save(repo, fe, "旅行", "")
    assert msg.startswith("💾")
    assert "2 件" in EB.folder_view(repo, ex)[2]                       # フォルダ一括エクスポートの対象件数
    assert folder_label(repo, sub) == "旅行"                           # ピッカーの表示名
    assert [it.path.name for it in list_folder(repo, sub)] == [n for n in FE.order_to_write(fe)]   # 並び順
    assert any("旅行" in label for label, _ in SB.listing(repo, {**SB.new_sv(), "cwd": "scenes"})[1])   # 保存ブロックの一覧
