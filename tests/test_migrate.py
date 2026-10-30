"""SPEC v2 の14章（移行ツール）と、11章の受け入れテスト15。"""
import hashlib
import json
from pathlib import Path

import pytest

from conftest import EXAMPLES, V1_SAMPLE_DATA, errors, warnings, write
from moa.core import Repository, compile_scene
from moa.core.repository import load_yaml
from moa.migrate_v2 import migrate, render_report
from moa.migrate_v2.__main__ import main
from moa.migrate_v2.convert import diff_tags, strip_leading_quality
from moa.migrate_v2.v1compile import compile_v1

V15 = EXAMPLES / "v1.5"


def tree_hash(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def read_v15():
    sid = "beach_chat__trio"
    neg = (V15 / f"{sid}_negative.txt").read_text(encoding="utf-8")
    return ((V15 / f"{sid}_moss.json").read_text(encoding="utf-8").strip(),
            (V15 / f"{sid}_anima.txt").read_text(encoding="utf-8").strip(),
            neg.split("[assembled MOSS negative]")[1].split("[assembled Anima negative]")[0].strip(),
            neg.split("[assembled Anima negative]")[1].strip())


# ---- 11-15 ----
def test_15_v15_samples_migrate_and_compile_identically(tmp_path):
    out = tmp_path / "out"
    result = migrate(V1_SAMPLE_DATA, out)

    repo = Repository(out)
    assert errors(repo.issues) == []                                  # v2 スキーマで全ファイルが通る
    assert [i.message for i in warnings(repo.issues)] == [
        "Action 'beach_chat' の anima に人数タグ/solo '3girls' があります（コンパイル時に除去します）"]   # 警告は1件

    moss, anima, neg_moss, neg_anima = read_v15()
    r = compile_scene(repo, repo.scene("beach_chat__trio"))
    assert r.ok
    assert r.moss_prompt == moss and r.anima_prompt == anima          # MOSS・Anima とも完全一致
    assert (r.negative_moss, r.negative_anima) == (neg_moss, neg_anima)

    assert len(result.scenes) == 1 and result.scenes[0].after.ok
    assert "**差分なし**" in render_report(result)


def test_the_embedded_v15_compiler_matches_the_v15_examples():
    """移行前（v1.5）の出力を作る最小の再実装が、v1.5 の期待出力と一致すること。"""
    base = V1_SAMPLE_DATA
    chars = {p.stem: load_yaml(p) for p in (base / "characters").rglob("*.yaml") if p.name != "_folder.yaml"}
    outfits = {p.stem: load_yaml(p) for p in (base / "outfits").rglob("*.yaml") if p.name != "_folder.yaml"}
    sit = load_yaml(base / "situations" / "beach" / "beach_chat.yaml")
    scene = load_yaml(base / "situations" / "beach" / "beach_chat__trio.yaml")
    b = compile_v1(chars, outfits, sit, scene)
    moss, anima, neg_moss, neg_anima = read_v15()
    assert (b.moss, b.anima, b.negative_moss, b.negative_anima) == (moss, anima, neg_moss, neg_anima)


# ---- 元のフォルダを変更しない ----
def test_the_source_folder_is_never_modified(tmp_path):
    src = tmp_path / "v1"
    import shutil
    shutil.copytree(V1_SAMPLE_DATA, src)
    before = tree_hash(src)
    mtimes = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
    assert main(["--src", str(src), "--out", str(tmp_path / "v2")]) == 0
    assert tree_hash(src) == before
    assert {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()} == mtimes
    assert sorted(p.name for p in src.iterdir()) == ["characters", "outfits", "situations"]   # 何も増えていない


def test_the_report_is_written_into_out(tmp_path):
    out = tmp_path / "v2"
    assert main(["--src", str(V1_SAMPLE_DATA), "--out", str(out)]) == 0
    text = (out / "MIGRATION_REPORT.md").read_text(encoding="utf-8")
    assert text.startswith("# v2 移行レポート") and "## 手作業が必要なもの" in text


# ---- --out の検査 ----
def test_nonempty_out_aborts_without_touching_anything(tmp_path):
    out = tmp_path / "taken"
    write(out / "keep.txt", "mine")
    assert main(["--src", str(V1_SAMPLE_DATA), "--out", str(out)]) == 2
    assert tree_hash(out) == {"keep.txt": hashlib.sha256(b"mine").hexdigest()}


def test_an_existing_empty_out_is_fine(tmp_path):
    out = tmp_path / "empty"
    out.mkdir()
    assert main(["--src", str(V1_SAMPLE_DATA), "--out", str(out)]) == 0 and (out / "scenes").is_dir()


@pytest.mark.parametrize("where", ["same", "inside", "parent"])
def test_out_must_be_separate_from_src(tmp_path, where):
    import shutil
    src = tmp_path / "v1"
    shutil.copytree(V1_SAMPLE_DATA, src)
    before = tree_hash(src)
    out = {"same": src, "inside": src / "new", "parent": tmp_path}[where]
    assert main(["--src", str(src), "--out", str(out)]) == 2
    assert tree_hash(src) == before


def test_missing_src_aborts(tmp_path):
    assert main(["--src", str(tmp_path / "nope"), "--out", str(tmp_path / "out")]) == 2


# ---- 合成した v1.x データで、14章の各項目を確認する ----
def moss_prompt(caption="{{girl}} {{girl.outfit}} She stands.", meta=("anime illustration", "safe", "festival scene"),
                items=None, **extra) -> str:
    tree = {"summary": {"meta": list(meta), "canvas": "single", "caption": caption},
            "items": items if items is not None else [{"label": "object", "caption": caption, "bbox": [-100, -100, 100, 100]}],
            "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx", **extra}
    return "\n".join("  " + line for line in json.dumps(tree, indent=2, ensure_ascii=False).splitlines())


def make_v1(root: Path, sit_id="booth", slots=("girl",), anima="masterpiece, best quality, score_7, safe, {{girl}}, {{girl.outfit}}, standing",
            folder="fest", **sit_extra):
    write(root / "characters" / "g.yaml", "id: g\nname: G\nmoss: |\n  Gina, an adult woman.\nanima: |\n  gina (test)\n")
    write(root / "characters" / "b.yaml", "id: b\nname: B\nmoss: |\n  Bea, an adult woman.\nanima: |\n  bea (test)\n")
    write(root / "outfits" / "suit.yaml", "id: suit\nname: S\nmoss: |\n  She wears a suit.\nanima: |\n  suit\n")
    sit = {"id": sit_id, "name": "展示", "tags": ["a", "b"], "rating": "safe", "slots": list(slots)}
    sit.update(sit_extra)
    lines = [f"id: {sit['id']}", f"name: {sit['name']}", "tags: [a, b]", f"rating: {sit['rating']}", f"slots: {json.dumps(list(slots))}",
             "canvas: { w: 1536, h: 1024 }"]
    if "outfits" in sit_extra:
        lines.append("outfits:")
        lines += [f"  {k}: {v if v is not None else 'null'}" for k, v in sit_extra["outfits"].items()]
    lines += ["mossPrompt: |", sit.get("_moss") or moss_prompt(), "animaPrompt: |", f"  {anima}",
              "negativeMoss: |", "  nm1, nm2", "negativeAnima: |", "  na1"]
    write(root / "situations" / folder / f"{sit_id}.yaml", "\n".join(lines) + "\n")
    return sit


def write_scene(root: Path, sid: str, situation: str, cast: dict, extra: str = "", folder="fest"):
    cast_lines = "\n".join(f"  {k}: {v}" for k, v in cast.items())
    write(root / "situations" / folder / f"{sid}.yaml",
          f"id: {sid}\nname: S\nsituation: {situation}\ncast:\n{cast_lines}\n{extra}")


def v2_of(tmp_path, build) -> tuple:
    src = tmp_path / "v1"
    build(src)
    out = tmp_path / "v2"
    result = migrate(src, out)
    return result, Repository(out), out


def test_situation_becomes_one_background_and_one_action(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "booth__g", "booth", {"girl": "g"})
    result, repo, out = v2_of(tmp_path, build)

    bg, action = repo.background("booth_bg"), repo.action("booth")
    assert errors(repo.issues) == []
    assert (bg.name, bg.rating, bg.canvas, bg.meta_tags) == ("展示（背景）", "safe", {"w": 1536, "h": 1024}, ["festival scene"])
    assert bg.moss_caption == "" and bg.moss_items == [] and bg.anima == ""
    assert (action.name, action.tags, action.rating, action.background, action.slots) == ("展示", ["a", "b"], "safe", "booth_bg", ["girl"])
    assert action.moss_caption.strip() == "{{girl}} {{girl.outfit}} She stands."
    assert [(i.label, i.bbox) for i in action.moss_items] == [("object", [-100, -100, 100, 100])]
    assert action.anima.strip() == "{{girl}}, {{girl.outfit}}, standing"           # 先頭の品質タグと rating 語は除く
    assert (action.negative_moss.strip(), action.negative_anima.strip()) == ("nm1, nm2", "na1")   # ネガティブは Action へ
    assert (out / "backgrounds" / "fest" / "booth_bg.yaml").exists() and (out / "actions" / "fest" / "booth.yaml").exists()
    assert repo.scene("booth__g").action == "booth" and repo.scene("booth__g").background == "booth_bg"
    assert (out / "scenes" / "fest" / "booth__g.yaml").exists()


def test_characters_and_outfits_are_copied_byte_for_byte(tmp_path):
    result, repo, out = v2_of(tmp_path, lambda src: make_v1(src))
    for rel in ("characters/g.yaml", "characters/b.yaml", "outfits/suit.yaml"):
        assert (out / rel).read_bytes() == (tmp_path / "v1" / rel).read_bytes()
    assert repo.character("g").sex == "female"                                     # sex は書き足さない
    assert "sex" not in (out / "characters" / "g.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("anima,expected,removed", [
    ("masterpiece, best quality, score_7, safe, a, b", "a, b", ["masterpiece", "best quality", "score_7", "safe"]),
    ("Masterpiece, score_9, score_8_up, NSFW, explicit, a", "a", ["Masterpiece", "score_9", "score_8_up", "NSFW", "explicit"]),
    ("a, masterpiece, best quality, b", "a, masterpiece, best quality, b", []),            # 先頭に並ぶものだけを除く
    ("masterpiece, a, safe, solo", "a, safe, solo", ["masterpiece"]),
    ("worst quality, a", "worst quality, a", []),
    ("", "", []),
])
def test_only_the_leading_quality_and_rating_tags_are_stripped(anima, expected, removed):
    assert strip_leading_quality(anima) == (expected, removed)


def test_meta_tags_drop_the_base_word_and_rating_words(tmp_path):
    def build(src):
        make_v1(src, rating="sensitive", _moss=moss_prompt(meta=("anime illustration", "nsfw", "sensitive", "festival scene", "beach")))
    _, repo, _ = v2_of(tmp_path, build)
    assert repo.background("booth_bg").meta_tags == ["festival scene", "beach"] and repo.background("booth_bg").rating == "sensitive"


def test_outfit_nulls_are_dropped_and_scene_outfits_are_merged_into_the_cast(tmp_path):
    def build(src):
        make_v1(src, slots=("girl", "boy"), outfits={"girl": "suit", "boy": None},
                anima="{{girl}}, {{boy}}, {{girl.outfit}}", _moss=moss_prompt("{{girl}} {{boy}}"))
        write_scene(src, "s1", "booth", {"girl": "g", "boy": "b"}, "outfits:\n  girl: none\n  boy: null\n")
        write_scene(src, "s2", "booth", {"girl": "g", "boy": "b"}, "outfits:\n  boy: suit\n")
    result, repo, _ = v2_of(tmp_path, build)
    assert errors(repo.issues) == []
    assert repo.action("booth").outfits == {"girl": "suit"}                          # null は捨てる
    assert {s: (e.character, e.outfit) for s, e in repo.scene("s1").cast.items()} == {"girl": ("g", "none"), "boy": ("b", None)}
    assert {s: (e.character, e.outfit) for s, e in repo.scene("s2").cast.items()} == {"girl": ("g", None), "boy": ("b", "suit")}
    assert any("null" in n for sc in result.scenes for n in sc.notes)
    assert "outfits" not in load_yaml(repo.path_of("scene", "s1"))                     # 別マップの outfits は残さない


def test_old_denoise_moves_to_gen_and_compiled_is_kept(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"},
                    "denoise: 0.6\ngen:\n  moss: { steps: 50, cfg: 3.0 }\ncompiled:\n  seed: 123\n  images: [a.png]\n"
                    "  mossPrompt: |\n    M\n  animaPrompt: |\n    A\n  negativeApplied: true\n  denoise: 0.6\n  weird: 1\n")
        write_scene(src, "s2", "booth", {"girl": "g"}, "denoise: 0.5\ngen:\n  anima: { denoise: 0.45 }\n")
    result, repo, out = v2_of(tmp_path, build)
    assert errors(repo.issues) == []
    assert repo.scene("s1").gen == {"moss": {"steps": 50, "cfg": 3.0}, "anima": {"denoise": 0.6}}
    assert repo.scene("s2").gen == {"anima": {"denoise": 0.45}}                       # gen が優先
    data = load_yaml(out / "scenes" / "fest" / "s1.yaml")
    assert "denoise" not in data
    assert data["compiled"] == {"seed": 123, "images": ["a.png"], "mossPrompt": "M\n", "animaPrompt": "A\n", "negativeApplied": True}
    notes = " ".join(n for sc in result.scenes if sc.id == "s1" for n in sc.notes)
    assert "compiled.denoise" in notes and "compiled.weird" in notes and "gen.anima.denoise" in notes


def test_an_out_of_range_seed_becomes_null_with_the_original_kept_as_a_comment(tmp_path):
    """v1.x のシードは 2^53 まで。v2 の範囲に収まる別の値では画像を再現できないので、null にして元の値を残す。"""
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"},
                    "compiled:\n  seed: 5029477155334195\n  images: [output/a.png, output/b.png]\n  negativeApplied: false\n")
    result, repo, out = v2_of(tmp_path, build)

    assert errors(repo.issues) == [] and repo.scene("s1") is not None                 # 読み込める
    path = out / "scenes" / "fest" / "s1.yaml"
    assert load_yaml(path)["compiled"]["seed"] is None
    assert load_yaml(path)["compiled"]["images"] == ["output/a.png", "output/b.png"]  # 画像のパスは残す
    assert "# v1.x のシード 5029477155334195" in path.read_text(encoding="utf-8")        # 元の値をコメントに残す
    note = " ".join(n for sc in result.scenes for n in sc.notes)
    assert "5029477155334195" in note and "null にしました" in note
    assert "null にしました" in render_report(result)


@pytest.mark.parametrize("seed", [0, 1, 4294967295])
def test_an_in_range_seed_is_left_alone(tmp_path, seed):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"}, f"compiled:\n  seed: {seed}\n  images: []\n")
    result, repo, out = v2_of(tmp_path, build)
    assert errors(repo.issues) == [] and repo.scene("s1") is not None
    assert load_yaml(out / "scenes" / "fest" / "s1.yaml")["compiled"]["seed"] == seed
    assert not any("seed" in n for sc in result.scenes for n in sc.notes)


@pytest.mark.parametrize("seed", [4294967296, -1])
def test_seeds_just_outside_the_range_are_nulled(tmp_path, seed):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"}, f"compiled:\n  seed: {seed}\n  images: []\n")
    _, repo, out = v2_of(tmp_path, build)
    assert errors(repo.issues) == [] and load_yaml(out / "scenes" / "fest" / "s1.yaml")["compiled"]["seed"] is None


def test_scene_comments_are_preserved(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"}, "applyNegative: false\n# 手で書いたメモ\n")
    _, _, out = v2_of(tmp_path, build)
    assert "# 手で書いたメモ" in (out / "scenes" / "fest" / "s1.yaml").read_text(encoding="utf-8")


def test_unreadable_moss_prompt_is_reported_and_the_rest_still_migrates(tmp_path):
    def build(src):
        make_v1(src, sit_id="bad", _moss="  {not json")
        make_v1(src, sit_id="good")
        write_scene(src, "s_bad", "bad", {"girl": "g"})
        write_scene(src, "s_good", "good", {"girl": "g"})
    result, repo, out = v2_of(tmp_path, build)
    bad = next(s for s in result.situations if s.id == "bad")
    assert bad.background_path is None and any("JSON" in e for e in bad.errors)
    assert repo.action("good") and repo.action("bad") is None
    assert repo.scene("s_good") is not None and repo.scene("s_bad") is not None        # Scene は落とさない（参照先なしで移行）
    assert any("参照先がありません" in n for sc in result.scenes if sc.id == "s_bad" for n in sc.notes)
    report = render_report(result)
    assert "## 移行できなかったもの" in report and "`bad`" in report.split("## 移行できなかったもの")[1]


def test_a_scene_pointing_at_a_missing_situation_is_migrated_and_flagged(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "orphan", "nowhere", {"girl": "g"})
    result, repo, _ = v2_of(tmp_path, build)
    assert repo.scene("orphan").action == "nowhere"
    assert any("Situation 'nowhere' が見つかりません" in n for sc in result.scenes for n in sc.notes)
    assert any("Action 'nowhere' が存在しません" in i.message for i in errors(repo.issues))


def test_discarded_fixed_values_and_dropped_items_are_reported(tmp_path):
    def build(src):
        items = [{"label": "object", "caption": "c", "bbox": [-100.0, -100.5, 100, 100], "score": 0.9}]
        make_v1(src, _moss=moss_prompt(items=items, canvas_hint="x").replace('"canvas": "single"', '"canvas": "multi"'))
    result, repo, _ = v2_of(tmp_path, build)
    s = result.situations[0]
    assert any("canvas = 'multi'" in d for d in s.discarded)
    assert "items[1].score" in s.dropped and "canvas_hint" in s.dropped
    assert any("-100.5" in c for c in s.coerced)
    assert repo.action("booth").moss_items[0].bbox == [-100, -100, 100, 100]      # -100.0 は整数に、-100.5 は丸め（-100）
    assert errors(repo.issues) == []


# ---- 人数タグのレポート（14章）----
def test_people_tags_left_in_the_action_are_reported_with_the_extras_hint(tmp_path):
    def build(src):
        make_v1(src, anima="masterpiece, best quality, score_7, safe, {{girl}}, 1boy, child, solo, standing")
        write_scene(src, "s1", "booth", {"girl": "g"})
    result, repo, _ = v2_of(tmp_path, build)
    s = result.situations[0]
    assert s.count_tags_left == ["1boy", "solo"] and s.had_people_tags
    report = render_report(result)
    assert "`1boy`, `solo`" in report and "`extras` を手で設定する必要があります" in report
    assert any("1boy" in i.message for i in warnings(repo.issues))                    # v2 では除去されて警告

    sc = result.scenes[0]
    d = diff_tags(sc.before.anima, sc.after.anima_prompt)
    assert d["removed"] == ["1boy"] and d["added"] == ["1girl"]                        # solo は元から1回。除去後に生成された分が残る
    assert "追加" in report and "除去 `1boy`" in report


def test_other_count_like_tags_get_the_others_hint_for_a_single_cast_action(tmp_path):
    def build(src):
        make_v1(src, anima="masterpiece, {{girl}}, 3children, children, 2people")
    result, _, _ = v2_of(tmp_path, build)
    s = result.situations[0]
    assert s.other_count_like == ["3children", "2people"] and not s.had_people_tags
    report = render_report(result)
    assert "`3children`, `2people`" in report and "`extras.others`" in report and "誤って `solo` が付きます" in report


def test_the_hint_is_softer_for_multi_cast_actions(tmp_path):
    def build(src):
        make_v1(src, slots=("girl", "boy"), anima="masterpiece, {{girl}}, {{boy}}, 3children", _moss=moss_prompt("{{girl}} {{boy}}"))
    result, _, _ = v2_of(tmp_path, build)
    section = render_report(result).split("## Situation ごと")[1]
    assert "必要なら `extras` を設定してください" in section and "誤って `solo` が付きます" not in section


def test_new_people_tags_are_reported_when_v1_had_none(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"})
    result, _, _ = v2_of(tmp_path, build)
    report = render_report(result)
    assert "**新しく付きます**" in report and "v2 で付く人数タグ：`1girl`, `solo`" in report
    assert "summary.caption の背景／行為への書き分けが残っています" in report


def test_the_before_and_after_diff_covers_moss_anima_and_negatives(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "g"})
    result, _, _ = v2_of(tmp_path, build)
    sc = result.scenes[0]
    assert sc.before is not None and sc.after.ok
    assert sc.before.negative_moss == sc.after.negative_moss and sc.before.negative_anima == sc.after.negative_anima
    assert json.loads(sc.before.moss)["summary"]["caption"] == json.loads(sc.after.moss_prompt)["summary"]["caption"]
    d = diff_tags(sc.before.anima, sc.after.anima_prompt)
    assert d["added"] == ["1girl", "solo"] and d["removed"] == []


# ---- フォルダ構成と _folder.yaml ----
def test_folder_layout_and_folder_meta_are_replicated_with_order_translated(tmp_path):
    def build(src):
        make_v1(src, folder="a/deep")
        write_scene(src, "s1", "booth", {"girl": "g"}, folder="a/deep")
        write(src / "situations" / "a" / "deep" / "_folder.yaml",
              "name: 深い\ndescription: 説明\norder:\n  - s1.yaml\n  - booth.yaml\n  - sub\n  - ghost.yaml\n")
    result, repo, out = v2_of(tmp_path, build)
    for root, fname in (("backgrounds", "booth_bg"), ("actions", "booth"), ("scenes", "s1")):
        assert (out / root / "a" / "deep" / f"{fname}.yaml").exists()
        meta = load_yaml(out / root / "a" / "deep" / "_folder.yaml")
        assert (meta["name"], meta["description"]) == ("深い", "説明")
    assert load_yaml(out / "backgrounds" / "a" / "deep" / "_folder.yaml")["order"] == ["booth_bg.yaml", "sub"]
    assert load_yaml(out / "actions" / "a" / "deep" / "_folder.yaml")["order"] == ["booth.yaml", "sub"]
    assert load_yaml(out / "scenes" / "a" / "deep" / "_folder.yaml")["order"] == ["s1.yaml", "sub"]
    assert len(result.folder_files) == 3 and errors(repo.issues) == []


def test_the_migrated_data_is_usable_by_the_app(tmp_path):
    def build(src):
        make_v1(src, slots=("girl", "boy"), anima="masterpiece, safe, {{girl}}, {{boy}}, {{girl.outfit}}",
                _moss=moss_prompt("{{girl}} {{boy}}"), outfits={"girl": "suit"})
        write_scene(src, "s1", "booth", {"girl": "g", "boy": "b"})
    result, repo, _ = v2_of(tmp_path, build)
    r = compile_scene(repo, repo.scene("s1"))
    assert r.ok and r.people_tags == "2girls" and "suit" in r.anima_prompt.split(", ")
    assert json.loads(r.moss_prompt)["summary"]["meta"][:2] == ["anime illustration", "safe"]


def test_the_report_lists_post_migration_issues(tmp_path):
    def build(src):
        make_v1(src)
        write_scene(src, "s1", "booth", {"girl": "nobody"})                           # 参照先のないキャラ
    result, repo, _ = v2_of(tmp_path, build)
    report = render_report(result)
    assert "移行前（v1.x）のコンパイルができませんでした" in report and "移行後（v2）のコンパイルができませんでした" in report
    assert "## 移行後データの検査" in report and "キャラクター 'nobody' が存在しません" in report
