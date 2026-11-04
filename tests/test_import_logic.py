"""インポート（EDITOR_SPEC 15章）のロジック。サンプルを一時フォルダにコピーした `repo` / `data_dir` に取り込む。実データには触れない。"""
import io
import zipfile
from pathlib import Path

import pytest

from conftest import SAMPLES, write
from moa.core import Repository
from moa.core.repository import load_yaml
from moa.core.yaml_io import atomic_create_text
from moa.editor.logic.importing import (Choices, apply_plan, inspect_batch, is_local_client, folder_dialog_blocked,
                                        read_batch, resolve, result_rows, rewrite_id, summary_md)
from moa.editor.logic.importing import source as S
from moa.editor.logic.importing.plan import (CREATE, F_CREATE, F_MERGE, F_REJECT, F_SKIP_LABEL, F_SKIP_SAME, REJECT,
                                             RENAMED, SKIP_CHOICE, SKIP_SAME, SKIP_UNSELECTED)

CHAR = "id: newchar\nname: 新キャラ\nmoss: m\nanima: a\n"                       # character と outfit の両方に適合する
BG = 'id: newbg\nname: 新背景\ncanvas: {w: 1536, h: 1024}\nmossCaption: ""\nanima: ""\n'
ACT = 'id: newact\nname: 新行為\nslots: [main]\nmossCaption: "{{main}}"\nmossItems: []\nanima: "{{main}}"\n'


def make_zip(tmp_path, files: dict, name="pack.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, content in files.items():
            zf.writestr(member, content)
    return path


def single(tmp_path, text, name="x.yaml") -> Path:
    return write(tmp_path / "up" / name, text)


def inspect(repo, source, choices=None):
    insp = inspect_batch(repo, read_batch(source))
    return insp, resolve(repo, insp, choices or Choices())


def do_import(repo, source, choices=None):
    insp = inspect_batch(repo, read_batch(source))
    choices = choices or Choices()
    return apply_plan(repo, insp, choices, confirmed=resolve(repo, insp, choices))


def by_path(plan, path):
    return plan.item(path)


# ---- 単一の .yaml ----
def test_a_single_yaml_is_imported_to_the_chosen_root_with_the_original_text(repo, data_dir, tmp_path):
    src = single(tmp_path, BG, "whatever_name.yaml")
    _, plan = inspect(repo, src)
    it = plan.items[0]
    assert (it.kind, it.action, it.by_choice) == ("background", CREATE, True) and plan.needs_dest == ["background"]
    done = do_import(repo, src)
    target = data_dir / "backgrounds" / "newbg.yaml"                       # ファイル名は <id>.yaml に揃える
    assert done.items[0].action == CREATE and target.read_text(encoding="utf-8") == BG
    assert Repository(data_dir).background("newbg").name == "新背景"
    assert not list(data_dir.rglob("*.tmp"))


def test_schema_mismatch_is_rejected_with_the_closest_kind_in_the_reason(repo, tmp_path):
    _, plan = inspect(repo, single(tmp_path, "id: x\nname: n\n"))
    it = plan.items[0]
    assert it.action == REJECT and "どの種類にも適合しません" in it.reason and "スキーマ違反" in it.reason


def test_a_numeric_id_is_rejected_with_the_quote_hint(repo, tmp_path):
    _, plan = inspect(repo, single(tmp_path, "id: 0901\nname: n\nmoss: m\nanima: a\n"))
    assert plan.items[0].action == REJECT and "IDが文字列でない（クォート漏れの疑い）" in plan.items[0].reason
    _, plan = inspect(repo, single(tmp_path, 'id: "0901"\nname: n\nmoss: m\nanima: a\n', "q.yaml"))   # クォートすれば取り込める
    assert plan.items[0].kind is None and plan.items[0].candidates                                      # （種類は選ぶ）


@pytest.mark.parametrize("text,reason", [
    ("id: Bad-Id\nname: n\nmoss: m\nanima: a\n", "^[a-z0-9_]+$"),
    ("id: a\nname: ''\nmoss: m\nanima: a\n", "name がありません"),
    ("- just\n- a list\n", "マッピングではありません"),
    ("id: [unclosed\n", "YAML として読めません"),
])
def test_unreadable_or_invalid_files_are_rejected_with_a_reason(repo, tmp_path, text, reason):
    _, plan = inspect(repo, single(tmp_path, text))
    assert plan.items[0].action == REJECT and reason in plan.items[0].reason


def test_a_non_utf8_file_is_rejected_and_a_bom_is_removed(repo, data_dir, tmp_path):
    bad = tmp_path / "up" / "sjis.yaml"
    bad.parent.mkdir()
    bad.write_bytes("id: a\nname: 日本語\nmoss: m\nanima: a\n".encode("cp932"))
    assert inspect(repo, bad)[1].items[0].reason == "UTF-8 として読めません"
    bom = tmp_path / "up" / "bom.yaml"
    bom.write_bytes(b"\xef\xbb\xbf" + BG.encode("utf-8"))
    do_import(repo, bom)
    assert (data_dir / "backgrounds" / "newbg.yaml").read_bytes() == BG.encode("utf-8")       # BOM は外して書く


def test_outfit_id_none_is_rejected(repo, tmp_path):
    _, plan = inspect(repo, make_zip(tmp_path, {"outfits/n.yaml": "id: none\nname: n\nmoss: m\nanima: a\n"}))
    assert plan.items[0].action == REJECT and "none" in plan.items[0].reason


def test_files_that_fail_the_editors_save_validation_are_rejected(repo, tmp_path):
    bad_bg = BG.replace('mossCaption: ""', 'mossCaption: "{{left}}"')                  # Background にプレイスホルダー
    bad_act = ACT.replace('anima: "{{main}}"', 'anima: "{{ghost}}"')                      # 宣言されていないスロット
    _, plan = inspect(repo, make_zip(tmp_path, {"backgrounds/b.yaml": bad_bg, "actions/a.yaml": bad_act}))
    assert [i.action for i in plan.items] == [REJECT, REJECT]
    assert "プレイスホルダー" in plan.items[0].reason and "ghost" in plan.items[1].reason


def test_only_yaml_files_are_imported_and_other_extensions_are_ignored(repo, tmp_path):
    src = make_zip(tmp_path, {"backgrounds/b.yaml": BG, "backgrounds/b.yml": BG.replace("newbg", "other"), "readme.txt": "hi"})
    insp, plan = inspect(repo, src)
    assert [i.path for i in plan.items] == ["backgrounds/b.yaml"]
    assert {ig.path for ig in plan.ignored} == {"backgrounds/b.yml", "readme.txt"}
    assert all("拡張子" in ig.reason for ig in plan.ignored)


def test_duplicate_ids_inside_the_batch_reject_every_file_with_that_kind_and_id(repo, tmp_path):
    other = BG.replace("名前", "x")
    src = make_zip(tmp_path, {"backgrounds/a/one.yaml": BG, "backgrounds/b/two.yaml": other,
                              "actions/newbg.yaml": ACT.replace("newact", "newbg")})            # 種類が違えば重複ではない
    _, plan = inspect(repo, src)
    one, two, act = plan.items
    assert one.action == two.action == REJECT and "同じ種類" in one.reason and "two.yaml" in one.reason
    assert act.action == CREATE


# ---- 既存と同じ id ----
def test_same_id_with_the_same_content_is_skipped_automatically(repo, data_dir, tmp_path):
    text = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": text.replace("\n", "\r\n")})     # 改行コードが違うだけ
    _, plan = inspect(repo, src)
    it = plan.items[0]
    assert it.action == SKIP_SAME and "すでに同じ内容があります" in it.reason and not it.conflict


def test_same_id_with_different_content_is_skipped_by_default_and_can_be_imported_with_a_new_id(repo, data_dir, tmp_path):
    original = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")
    changed = original.replace("name: カミツレ（BW版）", "name: 別物 # 取り込み側のコメント", 1)
    assert changed != original and "id: elesa_bw\n" in changed
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": changed})
    _, plan = inspect(repo, src)
    it = plan.items[0]
    assert it.action == SKIP_CHOICE and it.conflict and it.suggested == "elesa_bw_copy"

    path = "characters/elesa_bw.yaml"
    choices = Choices(rename={path: it.suggested})
    done = do_import(repo, src, choices)
    assert done.items[0].action == RENAMED
    written = (data_dir / "characters" / "elesa_bw_copy.yaml").read_text(encoding="utf-8")
    assert written == changed.replace("id: elesa_bw\n", "id: elesa_bw_copy\n", 1)         # id の行だけが違い、コメントは残る
    assert "# 取り込み側のコメント" in written
    assert (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8") == original   # 既存は無傷


def test_the_default_new_id_counts_up_when_the_copy_is_taken(repo, data_dir, tmp_path):
    write(data_dir / "characters" / "elesa_bw_copy.yaml", "id: elesa_bw_copy\nname: x\nmoss: m\nanima: a\n")
    repo.reload()
    changed = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8").replace("カミツレ（BW版）", "別物", 1)
    _, plan = inspect(repo, make_zip(tmp_path, {"characters/elesa_bw.yaml": changed}))
    assert plan.items[0].suggested == "elesa_bw_copy2"


@pytest.mark.parametrize("new,expect", [("Bad Id", "半角英小文字"), ("skyla_bw", "既に使われています")])
def test_the_new_id_gets_the_same_checks_as_a_new_entity(repo, data_dir, tmp_path, new, expect):
    changed = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8").replace("カミツレ（BW版）", "別物", 1)
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": changed})
    _, plan = inspect(repo, src, Choices(rename={"characters/elesa_bw.yaml": new}))
    assert plan.items[0].action == REJECT and expect in plan.items[0].reason


def test_an_unindexed_file_with_the_same_name_counts_as_an_existing_one(repo, data_dir, tmp_path):
    write(data_dir / "backgrounds" / "newbg.yaml", "this is not a valid background: [")
    repo.reload()
    _, plan = inspect(repo, single(tmp_path, BG))
    assert plan.items[0].conflict and plan.items[0].action == SKIP_CHOICE


# ---- 種類の判定 ----
def test_a_path_hint_decides_between_character_and_outfit(repo, data_dir, tmp_path):
    src = make_zip(tmp_path, {"characters/c.yaml": CHAR, "outfits/o.yaml": CHAR.replace("newchar", "newoutfit")})
    _, plan = inspect(repo, src)
    assert [(i.kind, i.action) for i in plan.items] == [("character", CREATE), ("outfit", CREATE)]


def test_a_path_that_contradicts_the_content_is_rejected(repo, tmp_path):
    _, plan = inspect(repo, make_zip(tmp_path, {"backgrounds/oops.yaml": CHAR}))
    assert plan.items[0].action == REJECT and "パスと中身が合いません" in plan.items[0].reason


def test_a_file_that_fits_both_character_and_outfit_asks_for_the_kind(repo, data_dir, tmp_path):
    src = single(tmp_path, CHAR)
    insp, plan = inspect(repo, src)
    it = plan.items[0]
    assert it.action == SKIP_UNSELECTED and it.candidates == ["character", "outfit"]
    chosen = resolve(repo, insp, Choices(kinds={"x.yaml": "outfit"}))
    assert chosen.items[0].kind == "outfit" and chosen.items[0].action == CREATE
    apply_plan(repo, insp, Choices(kinds={"x.yaml": "outfit"}))
    assert (data_dir / "outfits" / "newchar.yaml").exists() and not (data_dir / "characters" / "newchar.yaml").exists()
    assert not (data_dir / "outfits" / "x.yaml").exists()


# ---- 配置先 ----
def test_a_zip_with_root_names_maps_to_the_same_relative_paths_and_merges_into_existing_folders(repo, data_dir, tmp_path):
    (data_dir / "characters" / "pokemon").mkdir()
    src = make_zip(tmp_path, {"characters/pokemon/newchar.yaml": CHAR, "outfits/summer/o.yaml": CHAR.replace("newchar", "o1"),
                              "backgrounds/newbg.yaml": BG})
    _, plan = inspect(repo, src)
    assert plan.needs_dest == [] and all(not i.by_choice for i in plan.items)             # 配置先は選ばせない
    done = do_import(repo, src)
    assert all(i.action == CREATE for i in done.items)
    assert (data_dir / "characters" / "pokemon" / "newchar.yaml").is_file()               # 既存フォルダに合流
    assert (data_dir / "outfits" / "summer" / "o1.yaml").is_file()                        # 無いフォルダは作る
    assert (data_dir / "backgrounds" / "newbg.yaml").is_file()


def test_a_wrapper_folder_is_removed_automatically(repo, data_dir, tmp_path):
    src = make_zip(tmp_path, {"mypack/characters/a/newchar.yaml": CHAR, "mypack/backgrounds/newbg.yaml": BG})
    batch = read_batch(src)
    assert [f.path for f in batch.files] == ["characters/a/newchar.yaml", "backgrounds/newbg.yaml"]
    assert any("ラッパー mypack を外しました" in n for n in batch.notes)
    do_import(repo, src)
    assert (data_dir / "characters" / "a" / "newchar.yaml").is_file()


def test_a_zip_without_root_names_asks_for_a_destination_per_kind_and_keeps_the_relative_structure(repo, data_dir, tmp_path):
    (data_dir / "backgrounds" / "sub").mkdir()
    src = make_zip(tmp_path, {"pack/x/newbg.yaml": BG, "pack/newact.yaml": ACT})
    insp, plan = inspect(repo, src)
    assert plan.needs_dest == ["background", "action"]
    choices = Choices(dest={"background": "backgrounds/sub"})                              # action は選ばない（ルート直下）
    done = apply_plan(repo, insp, choices)
    assert (data_dir / "backgrounds" / "sub" / "pack" / "x" / "newbg.yaml").is_file()
    assert (data_dir / "actions" / "pack" / "newact.yaml").is_file()
    assert [i.action for i in done.items] == [CREATE, CREATE]


def test_a_destination_outside_the_kinds_root_is_ignored(repo, data_dir, tmp_path):
    insp, _ = inspect(repo, single(tmp_path, BG))
    plan = resolve(repo, insp, Choices(dest={"background": "characters"}))
    assert plan.items[0].dest == (data_dir / "backgrounds" / "newbg.yaml").resolve()


def test_folders_with_unusable_names_are_rejected_and_hidden_or_system_folders_are_ignored(repo, tmp_path):
    src = make_zip(tmp_path, {"characters/CON/a.yaml": CHAR, "characters/_hidden/b.yaml": CHAR.replace("newchar", "b"),
                              "characters/ok/c.yaml": CHAR.replace("newchar", "c"),
                              "__MACOSX/characters/._c.yaml": "junk", "characters/.git/d.yaml": CHAR.replace("newchar", "d")})
    _, plan = inspect(repo, src)
    assert [(i.path, i.action) for i in plan.items] == [("characters/CON/a.yaml", REJECT), ("characters/_hidden/b.yaml", REJECT),
                                                        ("characters/ok/c.yaml", CREATE)]
    assert "フォルダ名" in plan.items[0].reason and "予約名" in plan.items[0].reason
    assert {ig.path for ig in plan.ignored} == {"__MACOSX/characters/._c.yaml", "characters/.git/d.yaml"}


# ---- zip の安全性・部分成功 ----
def test_a_mixed_zip_imports_the_good_files_and_reports_every_other_file(repo, data_dir, tmp_path):
    src = make_zip(tmp_path, {"backgrounds/good.yaml": BG, "backgrounds/broken.yaml": "id: [",
                              "../evil.yaml": BG.replace("newbg", "evil"), "/abs/evil2.yaml": BG.replace("newbg", "evil2"),
                              "C:/win/evil3.yaml": BG.replace("newbg", "evil3"), "__MACOSX/._x.yaml": "x", "notes.txt": "t"})
    done = do_import(repo, src)
    assert [i.action for i in done.items] == [CREATE, REJECT]
    assert (data_dir / "backgrounds" / "newbg.yaml").is_file()
    assert not list(data_dir.parent.rglob("evil*.yaml"))                                    # 配置先の外には何も書かない
    reasons = {ig.path: ig.reason for ig in done.ignored}
    assert "配置先の外" in reasons["../evil.yaml"] and "絶対パス" in reasons["/abs/evil2.yaml"]
    assert "絶対パス" in reasons["C:/win/evil3.yaml"] and "システム用" in reasons["__MACOSX/._x.yaml"]
    assert "拡張子" in reasons["notes.txt"]
    rows = {r[0]: r for r in result_rows(repo, done, True)}                                   # 無言で落とさない
    assert rows["backgrounds/good.yaml"][1].startswith("取り込んだ（新規）") and rows["backgrounds/broken.yaml"][1] == "却下"
    assert rows["../evil.yaml"][1] == "無視" and "取り込んだ：1 件" in summary_md(done, True)


def test_zip_member_names_in_cp932_without_the_utf8_flag_are_decoded(repo, tmp_path):
    name = "characters/日本語.yaml"
    raw = name.encode("cp932")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("X" * len(raw), CHAR.replace("newchar", "nihongo"))               # ASCII の仮の名前で書き、あとで cp932 に置き換える
    data = buf.getvalue().replace(b"X" * len(raw), raw)
    path = tmp_path / "cp932.zip"
    path.write_bytes(data)
    batch = read_batch(path)
    assert [f.path for f in batch.files] == [name]
    path2 = make_zip(tmp_path, {name: CHAR.replace("newchar", "nihongo")}, "utf8.zip")          # UTF-8 のフラグ付きもそのまま読める
    assert [f.path for f in read_batch(path2).files] == [name]


def test_oversized_files_and_too_many_files_are_refused(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_FILE_BYTES", 50)
    insp, plan = inspect(repo, make_zip(tmp_path, {"backgrounds/big.yaml": BG + "# " + "x" * 100, "backgrounds/ok.yaml": "id: a\n"}))
    assert any("大きすぎます" in ig.reason for ig in plan.ignored)
    monkeypatch.setattr(S, "MAX_FILES", 1)
    assert "多すぎます" in read_batch(make_zip(tmp_path, {"a.yaml": "x", "b.yaml": "y"}, "many.zip")).error
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert "zip として読めません" in read_batch(bad).error
    assert "対応していない" in read_batch(tmp_path / "x.txt").error


def test_importing_a_folder_reads_yaml_files_recursively(repo, data_dir, tmp_path):
    root = tmp_path / "mydata"
    write(root / "characters" / "a" / "newchar.yaml", CHAR)
    write(root / "backgrounds" / "newbg.yaml", BG)
    write(root / "backgrounds" / ".hidden" / "z.yaml", BG)
    write(root / "readme.md", "x")
    batch = read_batch(root)
    assert sorted(f.path for f in batch.files) == ["backgrounds/newbg.yaml", "characters/a/newchar.yaml"]
    assert {ig.path for ig in batch.ignored} == {"backgrounds/.hidden", "readme.md"}
    do_import(repo, root)
    assert (data_dir / "characters" / "a" / "newchar.yaml").is_file() and (data_dir / "backgrounds" / "newbg.yaml").is_file()


def test_choosing_a_root_folder_itself_keeps_its_name_in_the_paths(repo, data_dir):
    batch = read_batch(SAMPLES / "data" / "characters")
    assert batch.files and all(f.path.startswith("characters/") for f in batch.files)


def test_importing_the_whole_sample_into_itself_skips_everything_as_the_same_content(repo):
    insp, plan = inspect(repo, SAMPLES / "data")
    assert len(plan.items) > 10 and all(i.action == SKIP_SAME for i in plan.items) and not plan.ignored


# ---- Scene と参照 ----
def scene_text(sid="newscene", background="newbg", action="newact", cast="main: {character: elesa_bw}"):
    return f"id: {sid}\nname: s\nbackground: {background}\naction: {action}\ncast:\n  {cast}\n"


def test_scene_references_inside_the_batch_are_resolved_without_warnings(repo, tmp_path):
    src = make_zip(tmp_path, {"backgrounds/newbg.yaml": BG, "actions/newact.yaml": ACT, "scenes/s.yaml": scene_text()})
    _, plan = inspect(repo, src)
    assert [i.action for i in plan.items] == [CREATE] * 3
    assert all(not i.warnings or i.path == "actions/newact.yaml" for i in plan.items)
    assert plan.item("scenes/s.yaml").warnings == []


def test_scene_references_that_exist_nowhere_are_warnings_not_rejections(repo, data_dir, tmp_path):
    src = make_zip(tmp_path, {"actions/newact.yaml": ACT,
                              "scenes/s.yaml": scene_text(background="nobg", cast="main: {character: nochar}")})
    done = do_import(repo, src)
    scene = done.item("scenes/s.yaml")
    assert scene.action == CREATE and (data_dir / "scenes" / "newscene.yaml").is_file()
    assert any("nobg" in w for w in scene.warnings) and any("nochar" in w for w in scene.warnings)
    assert all(w.startswith("参照先が見つかりません") for w in scene.warnings)


def test_a_scene_whose_cast_does_not_match_the_action_slots_is_rejected(repo, tmp_path):
    src = make_zip(tmp_path, {"backgrounds/newbg.yaml": BG, "actions/newact.yaml": ACT,
                              "scenes/s.yaml": scene_text(cast="left: {character: elesa_bw}")})
    _, plan = inspect(repo, src)
    scene = plan.item("scenes/s.yaml")
    assert scene.action == REJECT and "left" in scene.reason and "main" in scene.reason


def test_action_and_character_references_to_missing_things_are_warnings(repo, tmp_path):
    char = "id: newchar\nname: n\nmoss: m\nanima: a\ndefaultOutfit: ghost_outfit\n"
    act = ACT.replace("mossItems: []", "outfits: {main: ghost_outfit2}\nmossItems: []")
    _, plan = inspect(repo, make_zip(tmp_path, {"characters/c.yaml": char, "actions/a.yaml": act}))
    assert [i.action for i in plan.items] == [CREATE, CREATE]
    assert any("ghost_outfit" in w for w in plan.items[0].warnings) and any("ghost_outfit2" in w for w in plan.items[1].warnings)


def test_a_reference_to_a_file_that_was_renamed_is_warned_and_not_rewritten(repo, data_dir, tmp_path):
    changed = (data_dir / "actions" / "beach_solo.yaml").read_text(encoding="utf-8").replace("海辺で日光浴（ソロ）", "別の行為", 1)
    scene = scene_text("renamed_ref", "beach", "beach_solo", "solo: {character: elesa_bw}")
    src = make_zip(tmp_path, {"actions/beach_solo.yaml": changed, "scenes/s.yaml": scene})
    choices = Choices(rename={"actions/beach_solo.yaml": "beach_solo_copy"})
    done = do_import(repo, src, choices)
    assert done.item("actions/beach_solo.yaml").action == RENAMED
    sc = done.item("scenes/s.yaml")
    assert sc.action == CREATE and any("別 ID（beach_solo_copy）" in w and "自動では書き換え" in w for w in sc.warnings)
    assert "action: beach_solo\n" in (data_dir / "scenes" / "renamed_ref.yaml").read_text(encoding="utf-8")   # 参照は書き換えない


# ---- _folder.yaml ----
def meta(folder: Path) -> dict:
    return load_yaml(folder / "_folder.yaml")


def test_folder_meta_for_a_new_folder_is_written_as_is(repo, data_dir, tmp_path):
    text = "# コメント\nname: 新しいフォルダ\ndescription: 説明\n"
    src = make_zip(tmp_path, {"characters/newdir/_folder.yaml": text, "characters/newdir/newchar.yaml": CHAR})
    done = do_import(repo, src)
    assert done.folders[0].action == F_CREATE
    assert (data_dir / "characters" / "newdir" / "_folder.yaml").read_text(encoding="utf-8") == text
    assert Repository(data_dir).folder_meta[(data_dir / "characters" / "newdir").resolve()].name == "新しいフォルダ"


def test_a_folder_without_meta_follows_the_label_choice(repo, data_dir, tmp_path):
    (data_dir / "characters" / "plain").mkdir()
    src = make_zip(tmp_path, {"characters/plain/_folder.yaml": "name: 表示名\n", "characters/plain/newchar.yaml": CHAR})
    insp, plan = inspect(repo, src)
    assert plan.folders[0].action == F_SKIP_LABEL and plan.folders[0].label_choice            # 初期値は付けない
    apply_plan(repo, insp, Choices())
    assert not (data_dir / "characters" / "plain" / "_folder.yaml").exists()
    assert (data_dir / "characters" / "plain" / "newchar.yaml").is_file()
    repo.reload()
    insp, _ = inspect(repo, src)
    done = apply_plan(repo, insp, Choices(label={"characters/plain": True}))
    assert done.folders[0].action == F_CREATE and meta(data_dir / "characters" / "plain")["name"] == "表示名"


@pytest.mark.parametrize("existing", ["name: 既存の名前\n", "description: 名前なし\n"])
def test_a_different_name_rejects_the_folder_meta_and_keeps_the_existing_one(repo, data_dir, tmp_path, existing):
    folder = data_dir / "characters" / "named"
    write(folder / "_folder.yaml", existing)
    repo.reload()
    src = make_zip(tmp_path, {"characters/named/_folder.yaml": "name: 取り込み側の名前\ndescription: 新\n",
                              "characters/named/newchar.yaml": CHAR})
    done = do_import(repo, src)
    f = done.folders[0]
    assert f.action == F_REJECT and "取り込み側の名前" in f.reason and ("既存の名前" in f.reason or "（なし）" in f.reason)
    assert (folder / "_folder.yaml").read_text(encoding="utf-8") == existing                  # 既存は上書きしない
    assert (folder / "newchar.yaml").is_file()                                                  # ファイル自体は取り込む


def test_order_is_extended_only_when_the_existing_meta_has_an_order(repo, data_dir, tmp_path):
    with_order = data_dir / "characters" / "with_order"
    without = data_dir / "characters" / "without_order"
    write(with_order / "_folder.yaml", "name: 並びあり\norder:\n  - elesa_x.yaml\n")
    write(without / "_folder.yaml", "name: 並びなし\n")
    repo.reload()
    src = make_zip(tmp_path, {"characters/with_order/_folder.yaml": "name: 並びあり\norder: [ignored.yaml]\n",
                              "characters/with_order/newchar.yaml": CHAR,
                              "characters/without_order/_folder.yaml": "name: 並びなし\n",
                              "characters/without_order/c2.yaml": CHAR.replace("newchar", "c2")})
    do_import(repo, src)
    assert meta(with_order)["order"] == ["elesa_x.yaml", "newchar.yaml"]                         # 既存の並びの末尾に足す
    assert "order" not in meta(without)                                                           # 既存に無ければ作らない


def test_order_uses_the_new_file_name_of_a_renamed_file(repo, data_dir, tmp_path):
    folder = data_dir / "characters" / "ordered"
    write(folder / "_folder.yaml", "name: 並び\norder:\n  - first.yaml\n")
    original = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")
    changed = original.replace("カミツレ（BW版）", "別物", 1)
    repo.reload()
    src = make_zip(tmp_path, {"characters/ordered/_folder.yaml": "name: 並び\n", "characters/ordered/elesa_bw.yaml": changed})
    do_import(repo, src, Choices(rename={"characters/ordered/elesa_bw.yaml": "elesa_bw_copy"}))
    assert meta(folder)["order"] == ["first.yaml", "elesa_bw_copy.yaml"]


def test_description_follows_the_choice_and_an_empty_existing_one_is_filled_automatically(repo, data_dir, tmp_path):
    a, b = data_dir / "characters" / "desc_a", data_dir / "characters" / "desc_b"
    write(a / "_folder.yaml", "name: A\ndescription: 既存の説明\n")
    write(b / "_folder.yaml", "name: B\n")
    repo.reload()
    src = make_zip(tmp_path, {"characters/desc_a/_folder.yaml": "name: A\ndescription: 取り込み側の説明\n",
                              "characters/desc_b/_folder.yaml": "name: B\ndescription: 自動で入る\n"})
    insp, plan = inspect(repo, src)
    fa, fb = plan.folders
    assert fa.desc_conflict == ("既存の説明", "取り込み側の説明") and fa.action == F_SKIP_SAME    # 初期値は既存＝変化なし
    assert fb.desc_conflict is None and fb.action == F_MERGE                                       # 既存が空なら自動で取り込み側
    apply_plan(repo, insp, Choices(desc={"characters/desc_a": "import"}))
    assert meta(a)["description"] == "取り込み側の説明" and meta(a)["name"] == "A"
    assert meta(b)["description"] == "自動で入る"


def test_a_merge_that_changes_nothing_is_not_written(repo, data_dir, tmp_path):
    folder = data_dir / "characters" / "same"
    write(folder / "_folder.yaml", "# 既存のコメント\nname: 同じ\ndescription: 同じ説明\n")
    repo.reload()
    before = (folder / "_folder.yaml").stat().st_mtime_ns
    src = make_zip(tmp_path, {"characters/same/_folder.yaml": "name: 同じ\ndescription: 同じ説明\n"})
    done = do_import(repo, src)
    assert done.folders[0].action == F_SKIP_SAME
    assert (folder / "_folder.yaml").stat().st_mtime_ns == before
    assert (folder / "_folder.yaml").read_text(encoding="utf-8").startswith("# 既存のコメント")


def test_a_folder_meta_applies_only_to_folders_that_have_files_in_the_batch(repo, data_dir, tmp_path):
    src = make_zip(tmp_path, {"pack/_folder.yaml": "name: パック\n", "pack/newbg.yaml": BG, "lonely/_folder.yaml": "name: 孤立\n"})
    _, plan = inspect(repo, src)
    ok, lonely = plan.folders
    assert ok.dest_dir == (data_dir / "backgrounds" / "pack") and ok.action == F_CREATE
    assert lonely.action == F_REJECT and "取り込むファイルがありません" in lonely.reason


def test_a_broken_folder_meta_is_rejected(repo, tmp_path):
    _, plan = inspect(repo, make_zip(tmp_path, {"characters/x/_folder.yaml": "name: 1\nbogus: true\n",
                                                "characters/x/newchar.yaml": CHAR}))
    assert plan.folders[0].action == F_REJECT and "スキーマ違反" in plan.folders[0].reason


# ---- 書き込み直前の再確認・上書きしないこと ----
def test_an_id_saved_while_the_confirmation_was_open_is_not_overwritten(repo, data_dir, tmp_path):
    src = single(tmp_path, BG)
    insp = inspect_batch(repo, read_batch(src))
    confirmed = resolve(repo, insp, Choices())
    assert confirmed.items[0].action == CREATE
    mine = write(data_dir / "backgrounds" / "newbg.yaml", 'id: newbg\nname: あとから保存された\ncanvas: {w: 1, h: 1}\n'
                                                           'mossCaption: ""\nanima: ""\n')           # 確認中にエディタで保存された
    done = apply_plan(repo, insp, Choices(), confirmed=confirmed)
    assert done.items[0].action in (SKIP_CHOICE, REJECT) and not done.items[0].writes
    assert "あとから保存された" in mine.read_text(encoding="utf-8")                                       # 既存は無傷
    rows = result_rows(repo, done, True)
    assert not rows[0][1].startswith("取り込んだ")                                                         # 結果に出る（無言で落とさない）


def test_the_state_changing_after_confirmation_blocks_only_the_changed_files(repo, data_dir, tmp_path):
    src = make_zip(tmp_path, {"backgrounds/newbg.yaml": BG, "actions/newact.yaml": ACT})
    insp = inspect_batch(repo, read_batch(src))
    confirmed = resolve(repo, insp, Choices())
    write(data_dir / "backgrounds" / "newbg.yaml", "id: newbg\nname: x\ncanvas: {w: 1, h: 1}\nmossCaption: ''\nanima: ''\n")
    done = apply_plan(repo, insp, Choices(), confirmed=confirmed)
    assert [i.action for i in done.items] == [SKIP_CHOICE, CREATE]
    assert (data_dir / "actions" / "newact.yaml").is_file()


def test_the_file_that_appears_just_before_writing_is_never_overwritten(tmp_path):
    path = tmp_path / "a.yaml"
    atomic_create_text(path, "first\r\n")
    assert path.read_bytes() == b"first\r\n"                                                        # 改行コードもそのまま
    with pytest.raises(FileExistsError):
        atomic_create_text(path, "second")
    assert path.read_bytes() == b"first\r\n" and not list(tmp_path.glob("*.tmp"))


# ---- id の書き換え ----
@pytest.mark.parametrize("text", [
    "id: old\nname: n\n",
    'id: "old"\nname: n\n',
    "id: 'old'   # 末尾のコメント\nname: n\n",
    "# 先頭のコメント\nid: old\r\nname: n\r\nanima: |\r\n  id: old\r\n",
    "name: n\nid: old\n",
])
def test_rewrite_id_changes_only_the_id_value(text):
    new = rewrite_id(text, "old", "new_id")
    assert new.replace("new_id", "old", 1) == text
    assert load_yaml_text(new)["id"] == "new_id"


def load_yaml_text(text):
    from ruamel.yaml import YAML
    return YAML(typ="safe").load(text)


def test_rewrite_id_refuses_when_it_cannot_find_the_value():
    with pytest.raises(ValueError):
        rewrite_id("name: n\n", "old", "new")
    with pytest.raises(ValueError):
        rewrite_id("id: oldest\nname: n\n", "old", "new")


# ---- PC 以外からの接続 ----
def test_only_the_local_pc_may_open_the_folder_dialog():
    assert is_local_client("127.0.0.1") and is_local_client("::1") and is_local_client("::ffff:127.0.0.1")
    assert not is_local_client("192.168.0.5") and not is_local_client("") and not is_local_client(None)

    class Req:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})() if host else None

    assert folder_dialog_blocked(Req("127.0.0.1")) == ""
    assert "PC 以外" in folder_dialog_blocked(Req("10.0.0.2")) and "10.0.0.2" in folder_dialog_blocked(Req("10.0.0.2"))
    assert "確認できません" in folder_dialog_blocked(Req(None)) and "確認できません" in folder_dialog_blocked(None)


# ---- 上書き（ファイルごとに選んだものだけ。既定はスキップ）----
def _changed_elesa(data_dir):
    original = (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8")
    return original, original.replace("カミツレ（BW版）", "別物 # 取り込み側のコメント", 1)


def test_overwrite_replaces_only_the_chosen_file_with_the_original_text(repo, data_dir, tmp_path):
    from moa.editor.logic.importing.plan import OVERWRITE
    original, changed = _changed_elesa(data_dir)
    other = (data_dir / "characters" / "skyla_bw.yaml").read_text(encoding="utf-8")
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": changed, "characters/skyla_bw.yaml": other.replace("name:", "name: x", 1)})
    _, plan = inspect(repo, src)
    assert {i.action for i in plan.items} == {SKIP_CHOICE}                          # 既定はどちらもスキップ
    done = do_import(repo, src, Choices(overwrite={"characters/elesa_bw.yaml"}))
    assert by_path(done, "characters/elesa_bw.yaml").action == OVERWRITE
    assert by_path(done, "characters/skyla_bw.yaml").action == SKIP_CHOICE          # 選ばなかった方は触らない
    assert (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8") == changed   # コメントごとそのまま
    assert (data_dir / "characters" / "skyla_bw.yaml").read_text(encoding="utf-8") == other
    assert not list(data_dir.rglob("*.tmp")) and not (data_dir / "characters" / "elesa_bw_copy.yaml").exists()


def test_overwrite_writes_to_the_existing_file_even_in_a_subfolder_and_ignores_the_chosen_destination(repo, data_dir, tmp_path):
    write(data_dir / "backgrounds" / "sub" / "newbg.yaml", BG.replace("新背景", "古い"))
    repo.reload()
    src = single(tmp_path, BG)                                                         # ルート名が無い → 配置先を選ぶ種類
    done = do_import(repo, src, Choices(overwrite={"x.yaml"}, dest={"background": ""}))
    assert done.items[0].action == "overwrite" and done.needs_dest == []
    assert (data_dir / "backgrounds" / "sub" / "newbg.yaml").read_text(encoding="utf-8") == BG
    assert not (data_dir / "backgrounds" / "newbg.yaml").exists()


def test_overwrite_and_new_id_cannot_be_chosen_together(repo, data_dir, tmp_path):
    original, changed = _changed_elesa(data_dir)
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": changed})
    p = "characters/elesa_bw.yaml"
    done = do_import(repo, src, Choices(overwrite={p}, rename={p: "elesa_bw_copy"}))
    assert done.items[0].action == REJECT and "同時に選べません" in done.items[0].reason
    assert (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8") == original


def test_an_overwrite_that_fails_validation_is_rejected_and_leaves_the_existing_file(repo, data_dir, tmp_path):
    original = (data_dir / "actions" / "beach_chat.yaml").read_text(encoding="utf-8")
    broken = original.replace("slots: [left, center, right]", "slots: [l, center, right]")
    assert broken != original
    done = do_import(repo, make_zip(tmp_path, {"actions/beach_chat.yaml": broken}), Choices(overwrite={"actions/beach_chat.yaml"}))
    assert done.items[0].action == REJECT and "beach_chat__trio" in done.items[0].reason   # 参照中の Scene を壊すスロット削除は保存時と同じく却下
    assert (data_dir / "actions" / "beach_chat.yaml").read_text(encoding="utf-8") == original


def test_overwriting_a_used_background_warns_about_the_scenes(repo, data_dir, tmp_path):
    original = (data_dir / "backgrounds" / "beach.yaml").read_text(encoding="utf-8")
    changed = original + "\n# 変更\nmossCaption: 別の説明\n" if "mossCaption:" not in original else original.replace("mossCaption:", "mossCaption: 別 ", 1)
    _, plan = inspect(repo, make_zip(tmp_path, {"backgrounds/beach.yaml": changed}), Choices(overwrite={"backgrounds/beach.yaml"}))
    it = plan.items[0]
    assert it.action == "overwrite" and any("Scene が使っています" in w for w in it.warnings)


def test_an_existing_file_changed_after_confirmation_is_not_overwritten(repo, data_dir, tmp_path):
    original, changed = _changed_elesa(data_dir)
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": changed})
    insp = inspect_batch(repo, read_batch(src))
    choices = Choices(overwrite={"characters/elesa_bw.yaml"})
    confirmed = resolve(repo, insp, choices)
    (data_dir / "characters" / "elesa_bw.yaml").write_text(original + "# 画面を開いている間に保存された\n", encoding="utf-8")
    done = apply_plan(repo, insp, choices, confirmed=confirmed)
    assert done.items[0].action == REJECT and ("変わった" in done.items[0].reason or "変更" in done.items[0].reason)
    assert (data_dir / "characters" / "elesa_bw.yaml").read_text(encoding="utf-8").endswith("保存された\n")


def test_the_peek_shows_a_line_diff_and_both_full_texts_for_a_conflict(repo, data_dir, tmp_path):
    from moa.editor.logic.importing import report as R
    original, changed = _changed_elesa(data_dir)
    src = make_zip(tmp_path, {"characters/elesa_bw.yaml": changed + "# <script>x</script>\n"})
    insp, plan = inspect(repo, src)
    p = "characters/elesa_bw.yaml"
    assert R.conflict_paths(plan) == [p]
    html = R.peek_html(plan, insp, p, R.PEEK_DIFF)
    assert "<ins" in html and "<del" in html and "別物" in html and "カミツレ（BW版）" in html
    assert "<script>" not in html and "&lt;script&gt;" in html                         # エスケープされる
    assert "別物" in R.peek_html(plan, insp, p, R.PEEK_NEW) and "カミツレ（BW版）" in R.peek_html(plan, insp, p, R.PEEK_OLD)
    assert R.peek_html(plan, insp, "nothing.yaml", R.PEEK_DIFF) == ""                  # 競合でないものは出さない
    assert R.line_diff_rows(original, original) == []


def test_the_peek_folds_long_unchanged_runs():
    from moa.editor.logic.importing import report as R
    old = "\n".join(f"l{i}" for i in range(30))
    rows = R.line_diff_rows(old, old.replace("l15", "L15"))
    assert any(r.kind == "head" and "変更なし" in r.text for r in rows) and len(rows) < 20
