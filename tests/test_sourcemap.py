"""ソースマップ（SPEC 10.1章）：すべての Anima タグと MOSS の文字列に出どころが付く。"""
import json

import pytest

from moa.core import compile_scene
from moa.core.sourcemap import line_paths
from moa.core.textutil import split_tags

SCENES = ["beach_chat__trio", "beach_solo__elesa", "guilty_lineup__trio", "beach_wave_boy__elesa"]


@pytest.mark.parametrize("sid", SCENES)
def test_every_anima_tag_has_an_origin(repo, sid):
    r = compile_scene(repo, repo.scene(sid))
    assert r.ok
    tags = split_tags(r.anima_prompt)
    assert len(r.sources.anima) == len(tags)
    assert all(o is not None for o in r.sources.anima)


@pytest.mark.parametrize("sid", SCENES)
def test_every_moss_string_has_origins_covering_it(repo, sid):
    r = compile_scene(repo, repo.scene(sid))
    moss = json.loads(r.moss_prompt)
    sm = r.sources.moss

    def check(path, value):
        if isinstance(value, dict):
            for k, v in value.items():
                check(path + (k,), v)
        elif isinstance(value, list) and path[-1:] != ("bbox",):
            for i, v in enumerate(value):
                check(path + (i,), v)
        else:
            spans = sm.get(path)
            assert spans, f"出どころがありません：{path}"
            if isinstance(value, str):                    # 区間が文字列を隙間なく覆う
                assert spans[0][0] == 0 and spans[-1][1] == len(value), path
                assert all(a[1] == b[0] for a, b in zip(spans, spans[1:])), path

    check((), moss)


def test_origins_point_to_the_right_entities(repo):
    r = compile_scene(repo, repo.scene("beach_chat__trio"))
    sm = r.sources
    cap = sm.moss[("summary", "caption")]
    assert cap[0][2].label() == "Background beach の mossCaption"
    assert cap[-1][2].label() == "Action beach_chat の mossCaption"
    item = sm.moss[("items", 1, "caption")]
    kinds = {(o.kind, o.id, o.path, o.via) for _, _, o in item}
    assert ("character", "skyla_bw", "moss", "{{left}}") in kinds
    assert ("outfit", "skyla_pilot", "moss", "{{left.outfit}}") in kinds
    assert ("outfit", "elesa_bw_summer", "moss", "{{center.outfit}}") not in kinds     # center は Scene の none が勝つ
    assert sm.moss[("items", 0, "caption")][0][2].label() == "Background beach の mossItems[0].caption"
    assert sm.moss[("items", 1, "bbox")][0][2].label() == "Action beach_chat の mossItems[0].bbox"
    assert sm.moss[("summary", "canvas")][0][2].generated
    labels = [o.label() for o in sm.anima[:3]]
    assert labels == ["生成（quality）", "生成（quality）", "生成（quality）"]
    assert sm.anima[3].label() == "生成（rating）" and sm.anima[4].label() == "生成（people）"
    assert sm.anima[5].label().startswith("Character skyla_bw の anima（{{left}}）")


def test_solo_tag_is_generated(repo):
    r = compile_scene(repo, repo.scene("beach_solo__elesa"))
    tags = split_tags(r.anima_prompt)
    assert r.sources.anima[tags.index("solo")].label() == "生成（solo）"


def test_negative_origins_follow_the_layers(repo):
    r = compile_scene(repo, repo.scene("beach_chat__trio"))
    tags = split_tags(r.negative_anima)
    assert len(r.sources.negative_anima) == len(tags)
    assert r.sources.negative_anima[0].label() == "生成（negativeBase）"
    assert r.sources.negative_anima[tags.index("duplicate")].label() == "Action beach_chat の negativeAnima"


def test_line_paths_cover_every_line_of_the_json(repo):
    r = compile_scene(repo, repo.scene("beach_chat__trio"))
    lines = r.moss_prompt.splitlines()
    paths = line_paths(json.loads(r.moss_prompt))
    assert len(paths) == len(lines)
    i = next(n for n, l in enumerate(lines) if '"caption": "A sunny' in l)
    assert paths[i] == ("summary", "caption")
