"""シーン保存ブロック（SPEC 10.2章）。Gradio を起動せずに、状態遷移（moa.ui.saveblock）と保存（moa.ui.logic.save）を確認する。

サンプルは一時フォルダにコピーしたもの（conftest の data_dir / repo）。実データには触れない。
"""
import shutil

import pytest

from conftest import write
from moa.core import Repository
from moa.core.repository import load_yaml
from moa.ui import logic as L
from moa.ui import saveblock as SB

TRIO = "beach_chat__trio"
ELESA = "beach_solo__elesa"


def pick(repo, sc, nav, target, *choices, slot=None):
    nav = L.set_target(nav, target, slot)
    for c in choices:
        sc, nav, _ = L.browse(repo, sc, nav, c)
    return sc, nav


def open_scene(repo, scene_id=TRIO, folder=""):
    path = f"scenes/{folder}/{scene_id}.yaml" if folder else f"scenes/{scene_id}.yaml"
    return pick(repo, L.new_sc(), L.new_nav(), "scene", f"file:{path}")


def new_draft(repo, action="beach_chat"):
    sc, nav = pick(repo, L.new_sc(), L.new_nav(), "action", f"file:actions/{action}.yaml")
    cast = {"beach_chat": {"left": "skyla_bw", "center": "elesa_bw", "right": "hex_maniac_xy"},
            "beach_solo": {"solo": "elesa_bw"}}[action]
    for slot, cid in cast.items():
        sc, nav = pick(repo, sc, nav, "char", f"file:characters/{cid}.yaml", slot=slot)
    return sc, nav


def move_scene(data_dir, scene_id, folder):
    """サンプルの Scene を scenes/<folder>/ に移す（フォルダ位置に意味はない：8章）。"""
    dst = data_dir / "scenes" / folder
    dst.mkdir(parents=True, exist_ok=True)
    shutil.move(str(data_dir / "scenes" / f"{scene_id}.yaml"), str(dst / f"{scene_id}.yaml"))
    return Repository(data_dir)


def saved(data_dir, rel):
    return load_yaml(data_dir / "scenes" / rel)


def synced(repo, sc, sv=None):
    sv, new_id = SB.sync(repo, sv or SB.new_sv(), sc)
    return sv, new_id


def snapshot_for(repo, sc, **override):
    r = L.compile_view(repo, sc)
    snap = {"seed": 4242, "images": ["output/m.png", "output/a.png"], "partial": False, "failed_stage": None,
            "error": None, "moss_prompt": r.moss_prompt, "anima_prompt": r.anima_prompt,
            "negative_applied": r.apply_negative, "negative_moss": r.negative_moss,
            "negative_anima": r.negative_anima, "background": r.background_id, "action": r.action_id,
            "canvas": dict(r.canvas), "gen": {}, "moss_ref": None}
    snap.update(override)
    return snap


# ---- 初期フォルダ ----
def test_the_initial_folder_is_the_folder_of_the_opened_scene(data_dir):
    repo = move_scene(data_dir, TRIO, "pokemon")
    sc, _ = open_scene(repo, folder="pokemon")
    sv, _ = synced(repo, sc)
    assert sv["cwd"] == "scenes/pokemon"
    crumbs, folders, scenes = SB.listing(repo, sv)
    assert "pokemon" in crumbs and folders[0] == ("⬆ 上へ", "up")
    assert [v for _, v in scenes] == ["file:scenes/pokemon/beach_chat__trio.yaml"]


def test_a_new_scene_starts_in_the_folder_of_the_scene_opened_before_then_in_the_scenes_root(data_dir):
    repo = move_scene(data_dir, TRIO, "pokemon")
    sc, nav = open_scene(repo, folder="pokemon")
    sv, _ = synced(repo, sc)
    sc2, _ = pick(repo, sc, nav, "action", "file:actions/beach_solo.yaml")           # 新規作成に切り替える（開いている Scene はなくなる）
    assert sc2["scene_id"] is None
    sv2, _ = synced(repo, sc2, sv)
    assert sv2["cwd"] == "scenes/pokemon"                                            # このタブで直前に開いた Scene のフォルダ
    fresh, _ = synced(repo, new_draft(repo)[0])
    assert fresh["cwd"] == "scenes"                                                  # 何も開いていなければ scenes/ 直下


def test_a_saved_scene_becomes_the_opened_one_so_the_next_folder_is_where_it_was_saved(repo, data_dir):
    sc, _ = new_draft(repo)
    sv, _ = synced(repo, sc)
    assert SB.make_folder(repo, sv, "新作").startswith("📁")
    folder = data_dir / "scenes" / "新作"
    sc2, msg = L.save(repo, sc, L.SaveTarget("beach_chat__fresh", folder=folder), "")
    assert msg.startswith("💾")
    sv2, _ = synced(repo, sc2, sv)
    assert sv2["cwd"] == "scenes/新作"


def test_after_a_save_the_default_folder_is_the_last_saved_folder_even_if_another_scene_is_opened(data_dir):
    repo = move_scene(data_dir, TRIO, "pokemon")
    sc, nav = open_scene(repo, folder="pokemon")
    sv, _ = synced(repo, sc)
    assert sv["cwd"] == "scenes/pokemon" and sv["last_saved"] is None               # セッションの最初：開いている Scene のフォルダ

    assert SB.make_folder(repo, sv, "新作").startswith("📁")
    sc_saved, msg = L.save(repo, sc, L.SaveTarget("beach_chat__fresh", folder=data_dir / "scenes" / "新作"), "")
    assert msg.startswith("💾")
    sv = SB.note_saved(repo, sv, repo.path_of("scene", sc_saved["scene_id"]))
    assert sv["last_saved"] == "scenes/新作"

    other, _ = pick(repo, sc_saved, nav, "scene", f"file:scenes/{ELESA}.yaml")      # scenes/ 直下の別の Scene を開く
    sv2, _ = synced(repo, other, sv)
    assert sv2["cwd"] == "scenes/新作"                                               # その後：最後に保存したフォルダ（開いた Scene のフォルダではない）

    draft, _ = new_draft(repo)                                                       # 新規作成に切り替えても同じ
    assert synced(repo, draft, sv2)[0]["cwd"] == "scenes/新作"

    fresh, _ = synced(repo, other)                                                   # セッションが切れる（新しい状態）とリセット
    assert fresh["cwd"] == "scenes" and fresh["last_saved"] is None


def test_an_overwrite_also_updates_the_last_saved_folder_and_a_failed_save_does_not(repo, data_dir):
    repo = move_scene(data_dir, TRIO, "pokemon")
    sc, _ = open_scene(repo, folder="pokemon")
    sv, _ = synced(repo, sc)
    sc2, msg = L.save(repo, sc, SB.overwrite_target(repo, sc), "")
    assert msg.startswith("💾")
    sv = SB.note_saved(repo, sv, repo.path_of("scene", sc2["scene_id"]))
    assert sv["last_saved"] == "scenes/pokemon"
    assert SB.note_saved(repo, sv, None) == sv                                       # 保存できなかったとき（パスなし）は変えない


# ---- id の初期値 ----
def test_the_default_id_is_copy_for_an_opened_scene_and_default_for_a_new_one(repo):
    sc, _ = open_scene(repo)
    assert SB.default_scene_id(repo, sc) == "beach_chat__trio_copy"
    assert synced(repo, sc)[1] == "beach_chat__trio_copy"
    new, _ = new_draft(repo)
    assert SB.default_scene_id(repo, new) == "beach_chat__default"
    assert SB.default_scene_id(repo, L.new_sc()) == ""                               # Action も未選択なら空


def test_a_used_default_id_advances_to_the_smallest_free_number(repo, data_dir):
    def scene_file(sid):
        return (f"id: {sid}\nbackground: beach\naction: beach_chat\ncast:\n"
                "  left: {character: skyla_bw}\n  center: {character: elesa_bw}\n  right: {character: hex_maniac_xy}\n")

    sc, _ = open_scene(repo)
    write(data_dir / "scenes" / "beach_chat__trio_copy.yaml", scene_file("beach_chat__trio_copy"))
    write(data_dir / "scenes" / "deep" / "beach_chat__trio_copy3.yaml", scene_file("beach_chat__trio_copy3"))   # 別フォルダでも「使われている」
    write(data_dir / "scenes" / "beach_chat__trio_copy2.yaml", "this is not a scene: [")                         # 索引にないが存在する
    repo.reload()
    assert SB.default_scene_id(repo, sc) == "beach_chat__trio_copy4"

    new, _ = new_draft(repo)
    write(data_dir / "scenes" / "beach_chat__default.yaml", scene_file("beach_chat__default"))
    repo.reload()
    assert SB.default_scene_id(repo, new) == "beach_chat__default2"


def test_the_initial_values_are_rebuilt_only_when_a_scene_is_reopened_or_the_action_changes(repo):
    sc, nav = open_scene(repo)
    sv, new_id = synced(repo, sc)
    assert new_id == "beach_chat__trio_copy"
    sv = SB.move(repo, sv, "dir:scenes")                                             # 手で操作した状態
    sc_bg, _ = pick(repo, sc, nav, "background", "file:backgrounds/white_background.yaml")
    sv2, new_id = synced(repo, sc_bg, sv)
    assert new_id is None and sv2["cwd"] == sv["cwd"]                                # Background を変えても、手入力は残す
    sc_open, _ = open_scene(repo)
    sc_open["open_seq"] += 1                                                         # 同じ Scene を開き直した
    assert synced(repo, sc_open, sv2)[1] == "beach_chat__trio_copy"
    draft, _ = new_draft(repo)
    sv3, _ = synced(repo, draft, sv2)
    sc_other, _ = pick(repo, draft, L.new_nav(), "action", "file:actions/beach_solo.yaml")
    assert synced(repo, sc_other, sv3)[1] == "beach_solo__default"                   # Action を選び直すと作り直す


# ---- ［上書き保存］----
def test_overwrite_saves_to_the_opened_scene_without_an_id_and_without_confirmation(repo, data_dir):
    sc, _ = open_scene(repo)
    sc["cast"]["left"]["character"] = "elesa_bw"
    target = SB.overwrite_target(repo, sc)
    assert target is not None and target.path == data_dir.resolve() / "scenes" / f"{TRIO}.yaml"
    assert repo.rel(target.path) in SB.overwrite_label(repo, sc)
    sc2, msg = L.save(repo, sc, target, "")                                          # id は入力しない・確認も要らない
    assert msg.startswith("💾 保存しました：") and str(target.path) in msg
    assert Repository(data_dir).scene(TRIO).cast["left"].character == "elesa_bw"
    assert sc2["scene_id"] == TRIO


def test_overwrite_is_not_available_while_creating_a_new_scene(repo):
    sc, _ = new_draft(repo)
    assert SB.overwrite_target(repo, sc) is None
    assert "別名で保存" in SB.overwrite_label(repo, sc)
    assert SB.overwrite_target(repo, L.new_sc()) is None


# ---- ［別名で保存］----
def test_save_as_with_an_unused_id_creates_a_new_file_in_the_shown_folder(repo, data_dir):
    sc, _ = open_scene(repo)
    sv, new_id = synced(repo, sc)
    sv = SB.move(repo, sv, "dir:scenes")
    (data_dir / "scenes" / "pokemon").mkdir()
    repo.reload()
    sv = SB.move(repo, sv, "dir:scenes/pokemon")
    kind, folder, sid = SB.plan_saveas(repo, sv, new_id)
    assert (kind, sid) == ("new", "beach_chat__trio_copy") and folder == (data_dir / "scenes" / "pokemon").resolve()
    sc2, msg = L.save(repo, sc, L.SaveTarget(sid, folder=folder), "コピー")
    path = data_dir.resolve() / "scenes" / "pokemon" / "beach_chat__trio_copy.yaml"
    assert path.exists() and f"保存しました：`{path}`" in msg
    assert (sc2["scene_id"], sc2["scene_name"]) == ("beach_chat__trio_copy", "コピー")          # 保存した Scene に切り替わる
    assert (data_dir / "scenes" / f"{TRIO}.yaml").exists()                                     # 元は無傷


@pytest.mark.parametrize("bad", ["Bad Name", "日本語", "a-b", "", "  "])
def test_save_as_rejects_a_bad_id_format(repo, bad):
    kind, msg, _ = SB.plan_saveas(repo, SB.new_sv(), bad)
    assert kind == "error" and "半角英小文字" in msg


def test_selecting_an_existing_scene_fills_the_id_and_switches_to_its_folder(repo, data_dir):
    repo = move_scene(data_dir, TRIO, "pokemon")
    sv = SB.new_sv()
    sv2, sid = SB.select(repo, sv, "file:scenes/pokemon/beach_chat__trio.yaml")
    assert sid == TRIO and sv2["cwd"] == "scenes/pokemon" and sv2["selected"] == "scenes/pokemon/beach_chat__trio.yaml"
    assert SB.selected_value(repo, sv2) == "file:scenes/pokemon/beach_chat__trio.yaml"
    assert SB.select(repo, sv, "file:actions/beach_chat.yaml")[1] is None                       # Scene 以外は選べない
    assert SB.select(repo, sv, None) == (sv, None)


def test_overwriting_through_save_as_is_allowed_only_for_the_selected_file_and_needs_confirmation(repo, data_dir):
    sc, _ = new_draft(repo)
    sv, sid = SB.select(repo, SB.new_sv(), f"file:scenes/{ELESA}.yaml")
    kind, path, sid = SB.plan_saveas(repo, sv, sid)
    assert kind == "confirm" and path == data_dir.resolve() / "scenes" / f"{ELESA}.yaml"     # 書く前に確認を出す
    assert "上書き" in SB.confirm_text(SB.pack(repo, L.SaveTarget(sid, path=path), "confirm"))
    before = path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8") == before                                         # 確認の段階では書いていない
    sc2, msg = L.save(repo, sc, SB.unpack(repo, SB.pack(repo, L.SaveTarget(sid, path=path), "confirm")), "")
    assert msg.startswith("💾") and Repository(data_dir).scene(ELESA).action == "beach_chat"  # 確認後に上書きされた
    assert sc2["scene_id"] == ELESA


def test_an_existing_id_is_a_duplicate_error_unless_that_exact_file_was_selected(repo, data_dir):
    # 選ばずに入力しただけ
    kind, msg, _ = SB.plan_saveas(repo, SB.new_sv(), TRIO)
    assert kind == "error" and "既に使われています" in msg and f"scenes/{TRIO}.yaml" in msg
    # 別のフォルダにある同じ id（一覧で選んだフォルダのファイルではない）
    repo = move_scene(data_dir, TRIO, "pokemon")
    sv, _ = SB.select(repo, SB.new_sv(), f"file:scenes/{ELESA}.yaml")                           # scenes/ 直下の別 Scene を選んだ
    kind, msg, _ = SB.plan_saveas(repo, sv, TRIO)
    assert kind == "error" and "scenes/pokemon/beach_chat__trio.yaml" in msg
    # 選んだあとフォルダを動かすと、選択は解除される
    sv, _ = SB.select(repo, SB.new_sv(), "file:scenes/pokemon/beach_chat__trio.yaml")
    assert SB.plan_saveas(repo, sv, TRIO)[0] == "confirm"
    assert SB.plan_saveas(repo, SB.move(repo, sv, "up"), TRIO)[0] == "error"
    # 索引にない（読み込めない）同名ファイルも重複
    write(data_dir / "scenes" / "broken.yaml", "this is not a scene: [")
    repo.reload()
    kind, msg, _ = SB.plan_saveas(repo, SB.new_sv(), "broken")
    assert kind == "error" and "scenes/broken.yaml" in msg


def test_moving_folders_stays_inside_scenes_and_drops_a_pending_confirmation(repo, data_dir):
    sv = SB.new_sv()
    assert SB.move(repo, sv, "up")["cwd"] == "scenes"                                            # scenes/ から出ない
    assert SB.move(repo, sv, "dir:characters")["cwd"] == "scenes"
    assert SB.move(repo, sv, "dir:scenes/../characters")["cwd"] == "scenes"
    (data_dir / "scenes" / "a").mkdir()
    pending = SB.pack(repo, L.SaveTarget("x", path=data_dir / "scenes" / f"{TRIO}.yaml"), "confirm")
    moved = SB.move(repo, SB.set_pending(sv, pending), "dir:scenes/a")
    assert moved["cwd"] == "scenes/a" and moved["pending"] is None                               # 行き先を変えたら確認待ちは取り消す
    ext = SB.pack(repo, L.SaveTarget("x", path=data_dir / "scenes" / f"{TRIO}.yaml"), "external")
    assert SB.move(repo, SB.set_pending(sv, ext), "dir:scenes/a")["pending"] == ext              # 外部変更待ちは残す


# ---- 保存する内容 ----
@pytest.mark.parametrize("how", ["overwrite", "new"])
def test_the_compiled_snapshot_is_written_on_every_save(repo, data_dir, how):
    sc, _ = open_scene(repo)
    sc["cast"]["left"]["character"] = "elesa_bw"
    target = SB.overwrite_target(repo, sc) if how == "overwrite" else L.SaveTarget("snap_new", folder=repo.root_dir("scene"))
    L.save(repo, sc, target, "")
    sid = TRIO if how == "overwrite" else "snap_new"
    c = saved(data_dir, f"{sid}.yaml")["compiled"]
    expected = L.compile_view(repo, sc)
    assert (c["background"], c["action"], c["negativeApplied"]) == ("beach", "beach_chat", False)
    assert (dict(c["canvas"]), c["rating"]) == (expected.canvas, expected.rating)
    assert c["mossPrompt"].strip() == expected.moss_prompt.strip() and c["animaPrompt"].strip() == expected.anima_prompt.strip()


def test_a_matching_generation_is_recorded_and_a_mismatch_is_a_warning_that_does_not_stop_the_save(repo, data_dir):
    sc, _ = open_scene(repo)
    snap = snapshot_for(repo, sc)
    _, msg = L.save(repo, sc, L.SaveTarget("gen_ok", folder=repo.root_dir("scene")), "", snap)
    c = saved(data_dir, "gen_ok.yaml")["compiled"]
    assert "🖼" in msg and (c["seed"], c["images"]) == (4242, ["output/m.png", "output/a.png"])

    sc["cast"]["center"]["character"] = "skyla_bw"                                              # 生成の後にキャストを変えた
    _, msg = L.save(repo, sc, L.SaveTarget("gen_ng", folder=repo.root_dir("scene")), "", snap)
    c = saved(data_dir, "gen_ng.yaml")["compiled"]
    assert msg.startswith("💾 保存しました：") and "⚠" in msg and "記録していません" in msg      # 保存は完了し、警告が出る
    assert "mossPrompt" in msg and "animaPrompt" in msg                                          # 違った項目を挙げる
    assert c["seed"] is None and c["images"] == [] and "gen" not in c                           # 生成の記録は書かない
    assert c["mossPrompt"]                                                                       # スナップショットは書く


def test_a_background_or_action_change_after_generation_is_named(repo, data_dir):
    sc, nav = open_scene(repo)
    snap = snapshot_for(repo, sc)
    sc2, _ = pick(repo, sc, nav, "background", "file:backgrounds/white_background.yaml")
    _, msg = L.save(repo, sc2, L.SaveTarget("bg_changed", folder=repo.root_dir("scene")), "", snap)
    assert "Background" in msg and "記録していません" in msg


def test_overwriting_with_changed_content_drops_the_old_generation_record_unless_this_generation_matches(repo, data_dir):
    sc, _ = open_scene(repo)
    target = SB.overwrite_target(repo, sc)
    L.save(repo, sc, target, "", snapshot_for(repo, sc))                                         # 記録つきで保存
    assert saved(data_dir, f"{TRIO}.yaml")["compiled"]["seed"] == 4242
    repo.reload()

    sc2, _ = open_scene(repo)
    sc2["cast"]["center"]["character"] = "skyla_bw"                                              # 内容を変えて上書き（生成なし）
    L.save(repo, sc2, SB.overwrite_target(repo, sc2), "")
    c = saved(data_dir, f"{TRIO}.yaml")["compiled"]
    assert c["seed"] is None and c["images"] == [] and "gen" not in c and "partial" not in c    # 古い記録は消える
    repo.reload()

    sc3, _ = open_scene(repo)
    L.save(repo, sc3, SB.overwrite_target(repo, sc3), "", snapshot_for(repo, sc3, seed=7))       # 今回の生成と一致 → 新しい記録
    assert saved(data_dir, f"{TRIO}.yaml")["compiled"]["seed"] == 7


def test_a_quick_edit_generation_is_not_recorded_and_the_edited_text_never_reaches_the_yaml(repo, data_dir):
    sc, _ = open_scene(repo)
    r = L.compile_view(repo, sc)
    snap = snapshot_for(repo, sc, anima_prompt=r.anima_prompt + ", SWEAT_EDIT",
                        quick_edit={"fields": ["anima"], "base": {"moss": r.moss_prompt, "anima": r.anima_prompt,
                                                                   "negm": r.negative_moss, "nega": r.negative_anima}})
    _, msg = L.save(repo, sc, L.SaveTarget("quick", folder=repo.root_dir("scene")), "", snap)
    text = (data_dir / "scenes" / "quick.yaml").read_text(encoding="utf-8")
    assert "SWEAT_EDIT" not in text and "試し打ち" in msg and "⚠" in msg and msg.startswith("💾")
    c = saved(data_dir, "quick.yaml")["compiled"]
    assert c["seed"] is None and c["images"] == []


def test_saving_keeps_the_open_sequence_so_quick_edits_and_gen_values_survive(repo):
    sc, _ = open_scene(repo)
    sc2, _ = L.save(repo, sc, SB.overwrite_target(repo, sc), "")
    assert sc2["open_seq"] == sc["open_seq"]


# ---- 外部変更の検知は両方の保存に効く ----
def test_external_change_stops_both_kinds_of_save_and_force_writes(repo, data_dir):
    import os
    path = data_dir / "scenes" / f"{TRIO}.yaml"
    sc, _ = open_scene(repo)
    other = data_dir / "scenes" / f"{ELESA}.yaml"
    sv, sid = SB.select(repo, SB.new_sv(), f"file:scenes/{ELESA}.yaml")                         # 別名保存で上書きする側も
    for p in (path, other):
        p.write_text(p.read_text(encoding="utf-8") + "# 外部で追記\n", encoding="utf-8")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    _, msg = L.save(repo, sc, SB.overwrite_target(repo, sc), "外部変更の後")
    assert msg.startswith(L.EXTERNAL_PREFIX) and "外部変更の後" not in path.read_text(encoding="utf-8")
    _, msg = L.save(repo, sc, L.SaveTarget(sid, path=other.resolve()), "")
    assert msg.startswith(L.EXTERNAL_PREFIX)
    _, msg = L.save(repo, sc, SB.overwrite_target(repo, sc), "外部変更の後", force=True)
    assert msg.startswith("💾") and load_yaml(path)["name"] == "外部変更の後"


def test_pack_and_unpack_round_trip_a_target(repo, data_dir):
    for target in (L.SaveTarget("a", path=repo.root_dir("scene") / f"{TRIO}.yaml"),
                   L.SaveTarget("b", folder=repo.root_dir("scene") / "pokemon")):
        assert SB.unpack(repo, SB.pack(repo, target, "external")) == L.SaveTarget(
            target.scene_id, path=target.path, folder=target.folder)
    assert SB.unpack(repo, None) is None and SB.confirm_text(None) == ""


# ---- ［＋フォルダ作成］----
def test_folder_creation_from_the_save_block_uses_the_shared_name_rules(repo, data_dir):
    sv = SB.new_sv()
    for bad in ("CON", "con.txt", "_x", ".x", "a?b", "末尾.", ""):
        assert SB.make_folder(repo, sv, bad).startswith("⚠"), bad
    assert SB.make_folder(repo, sv, "ポケモン").startswith("📁")
    assert (data_dir / "scenes" / "ポケモン").is_dir()
    assert SB.make_folder(repo, sv, "ポケモン").startswith("⚠")                                  # 同名は不可
    assert ("📁 ポケモン", "dir:scenes/ポケモン") in SB.listing(repo, sv)[1]                     # 一覧に出る
    inner = SB.move(repo, sv, "dir:scenes/ポケモン")
    assert SB.make_folder(repo, inner, "内側").startswith("📁")                                  # 表示中のフォルダの中に作る
    assert (data_dir / "scenes" / "ポケモン" / "内側").is_dir()


def test_scenes_in_the_list_are_those_of_the_shown_folder_only(repo, data_dir):
    _, _, scenes = SB.listing(repo, SB.new_sv())
    assert {v for _, v in scenes} == {f"file:scenes/{n}.yaml" for n in (TRIO, ELESA, "beach_wave_boy__elesa", "guilty_lineup__trio")}
    assert all(label.startswith("🎬") for label, _ in scenes)
