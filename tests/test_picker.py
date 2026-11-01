"""共通ピッカー（moa.core.picker / moa.editor.logic.picker）とビルダー共有ブラウザの検索。"""
from conftest import write, y
from moa.core import Repository
from moa.core import picker as core
from moa.editor.logic import picker as PK
from moa.ui import logic as L


def make_outfit(data_dir, oid, folder="", name=None, tags=()):
    path = data_dir / "outfits" / folder / f"{oid}.yaml" if folder else data_dir / "outfits" / f"{oid}.yaml"
    return write(path, f"id: {oid}\nname: {y(name or oid)}\ntags: {y(list(tags))}\nmoss: m\nanima: a\n")


def tree(data_dir):
    """outfits/summer/（表示名「夏服」）に2件、outfits/summer/beach/（表示名なし）に1件。"""
    write(data_dir / "outfits" / "summer" / "_folder.yaml", 'name: "夏服"\norder: [zz_last.yaml, aa_first.yaml]\n')
    make_outfit(data_dir, "aa_first", "summer", "ビキニ", ["bikini_tag"])
    make_outfit(data_dir, "zz_last", "summer", "ワンピース")
    make_outfit(data_dir, "deep_one", "summer/beach", "砂浜の服")
    return Repository(data_dir)


def root_of(repo):
    return (repo.root / "outfits").resolve()


# ---- core：フォルダ移動・パンくず ----
def test_folder_view_lists_subfolders_and_direct_entities_in_spec_order(data_dir):
    repo = tree(data_dir)
    top = core.folder_view(repo, root_of(repo), root_of(repo))
    assert top.folders == [("夏服", "outfits/summer")]
    assert all(label.endswith(f"（{eid}）") for label, eid in top.entities)
    assert "aa_first" not in [eid for _, eid in top.entities]   # 直下だけ（サブフォルダの中は出ない）

    sub = core.folder_view(repo, root_of(repo), root_of(repo) / "summer")
    assert [eid for _, eid in sub.entities] == ["zz_last", "aa_first"]   # _folder.yaml の order が先
    assert sub.entities[0][0] == "ワンピース（zz_last）"
    assert sub.folders == [("beach", "outfits/summer/beach")]


def test_breadcrumbs_use_the_folder_yaml_name(data_dir):
    repo = tree(data_dir)
    fv = core.folder_view(repo, root_of(repo), root_of(repo) / "summer" / "beach")
    assert fv.crumbs == [("outfits", "outfits"), ("夏服", "outfits/summer"), ("beach", "outfits/summer/beach")]


def test_entities_use_natural_order(data_dir):
    for n in (10, 2, 1):
        make_outfit(data_dir, f"nat{n}")
    repo = Repository(data_dir)
    ids = [eid for _, eid in core.folder_view(repo, root_of(repo), root_of(repo)).entities if eid.startswith("nat")]
    assert ids == ["nat1", "nat2", "nat10"]


# ---- core：検索 ----
def test_search_matches_name_id_and_tags_and_shows_the_folder_path(data_dir):
    repo = tree(data_dir)
    rows, total = core.search(repo, root_of(repo), "ビキニ")
    assert total == 1 and rows[0][0].id == "aa_first" and rows[0][1] == "outfits / 夏服"
    assert core.search(repo, root_of(repo), "DEEP_ONE")[1] == 1                    # ID・大文字小文字を区別しない
    assert core.search(repo, root_of(repo), "bikini_tag")[0][0][0].id == "aa_first"       # tags
    rows, _ = core.search(repo, root_of(repo), "砂浜")
    assert rows[0][1] == "outfits / 夏服 / beach"
    assert core.search(repo, root_of(repo), "  ") == ([], 0)


def test_search_stops_at_100_and_reports_the_total(data_dir):
    for i in range(120):
        make_outfit(data_dir, f"bulk{i:03d}", "bulk", f"まとめ{i}")
    repo = Repository(data_dir)
    rows, total = core.search(repo, root_of(repo), "まとめ")
    assert len(rows) == 100 and total == 120
    assert rows[0][1] == "outfits / bulk"
    assert core.search_message(len(rows), total) == "全 120 件中、先頭 100 件を表示"
    assert core.search_message(3, 3) == "3 件" and core.search_message(0, 0) == "該当なし"


# ---- core：開いたときのフォルダ ----
def test_initial_folder_is_the_selected_items_folder_else_last_else_root(data_dir):
    repo = tree(data_dir)
    root = root_of(repo)
    assert core.initial_folder(repo, root, "deep_one", "outfit", "outfits/summer") == root / "summer" / "beach"
    assert core.initial_folder(repo, root, None, "outfit", "outfits/summer") == root / "summer"
    assert core.initial_folder(repo, root, "nothing", "outfit", "outfits/summer") == root / "summer"
    assert core.initial_folder(repo, root, None, "outfit", None) == root
    assert core.initial_folder(repo, root, None, "outfit", "outfits/gone") == root       # 消えたフォルダ
    assert core.initial_folder(repo, root, None, "outfit", "characters") == root         # ルートの外


# ---- エディタのロジック ----
def test_editor_picker_opens_navigates_searches_and_picks(data_dir):
    repo = tree(data_dir)
    pk = PK.open_picker(repo, PK.new_pk(), "def", "deep_one")
    assert pk["cwd"] == "outfits/summer/beach"
    v = PK.view(repo, pk)
    assert v["target_md"] == "**選択先：defaultOutfit**" and v["items"] == [("砂浜の服（deep_one）", "deep_one")]

    pk = PK.enter(repo, pk, "outfits/summer")
    assert [i for _, i in PK.view(repo, pk)["items"]] == ["zz_last", "aa_first"]
    pk = PK.set_query(pk, "bikini_tag")
    v = PK.view(repo, pk)
    assert v["dirs"] == [] and v["items"] == [("ビキニ（aa_first）　📁 outfits / 夏服", "aa_first")]
    assert PK.enter(repo, pk, "outfits")["query"] == ""                              # フォルダ移動で検索は解除

    pk = PK.pick(repo, pk, "aa_first")
    assert pk["target"] is None and pk["last"]["outfits"] == "outfits/summer"
    reopened = PK.open_picker(repo, pk, "o0", None)                                  # 未選択 → 前回のフォルダ
    assert reopened["cwd"] == "outfits/summer" and reopened["label"] == "outfits.slot0"


def test_shown_text(data_dir):
    repo = tree(data_dir)
    assert PK.shown(repo, "outfit", "aa_first") == "ビキニ（aa_first）"
    assert PK.shown(repo, "outfit", "x", mode="unset") == "未指定"
    assert PK.shown(repo, "outfit", None, mode="none") == "none（着せない）"
    assert PK.shown(repo, "outfit", "ghost") == "ghost（見つかりません）"
    assert PK.shown(repo, "background", "", empty="（自動）") == "（自動）"


# ---- ビルダーの共有ブラウザ ----
def test_builder_browser_search_is_flat_with_paths_and_selectable(data_dir):
    repo = tree(data_dir)
    nav = L.set_target(L.new_nav(), "outfit", "a")
    nav = L.set_query(nav, "ビキニ")
    choices, crumbs = L.listing(repo, nav)
    assert choices == [("ビキニ（aa_first）　📁 outfits / 夏服", "file:outfits/summer/aa_first.yaml")]
    assert "検索：ビキニ" in crumbs and "1 件" in crumbs
    sc, nav2, msg = L.browse(repo, L.new_sc(), nav, choices[0][1])
    assert sc["cast"]["a"]["outfit"] == "aa_first"

    assert L.listing(repo, L.set_query(nav, ""))[0][0][1].startswith("dir:")          # 空に戻すとフォルダ表示
    assert L.set_target(nav, "char", "a")["query"] == ""                              # 選択先を変えると解除


def test_builder_browser_search_is_off_for_the_save_destination(data_dir):
    repo = tree(data_dir)
    nav = L.set_query(L.set_target(L.new_nav(), "savedir"), "abc")
    choices, crumbs = L.listing(repo, nav)
    assert choices == [] and "フォルダ表示" in crumbs
