import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moa.core import CastEntry, Repository, Scene, compile_scene  # noqa: E402

SPEC_V2 = ROOT / "spec_v2"
SAMPLES = SPEC_V2 / "samples"          # 現行仕様（v2）のサンプル。テストはこれを参照する
EXAMPLES = SAMPLES / "examples"
V1_SAMPLE_DATA = ROOT / "spec" / "samples" / "data"   # 旧仕様（v1.5）のサンプル。移行テストの入力


@pytest.fixture
def data_dir(tmp_path) -> Path:
    """spec_v2/samples/data を tmp にコピーしたもの（テスト中に自由に編集してよい）。"""
    dst = tmp_path / "data"
    shutil.copytree(SAMPLES / "data", dst)
    return dst


@pytest.fixture
def repo(data_dir) -> Repository:
    return Repository(data_dir)


@pytest.fixture
def trio(repo) -> Scene:
    return repo.scene("beach_chat__trio")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def errors(issues):
    return [i for i in issues if i.level == "error"]


def warnings(issues):
    return [i for i in issues if i.level == "warning"]


def y(value) -> str:
    """YAML に埋め込める文字列（JSON の文字列は YAML の二重引用符文字列として有効）。"""
    return json.dumps(value, ensure_ascii=False)


def make_character(data_dir: Path, cid: str, moss: str | None = None, anima: str | None = None, **fields) -> Path:
    """characters/ に最小限のキャラを書く。fields は YAML のキー（sex、defaultOutfit、bodyHairMoss …）。"""
    lines = [f"id: {cid}", f"name: {y(cid)}",
             f"moss: {y(moss if moss is not None else f'{cid} appears here.')}",
             f"anima: {y(anima if anima is not None else f'{cid} (test)')}"]
    lines += [f"{k}: {y(v)}" for k, v in fields.items()]
    return write(data_dir / "characters" / f"{cid}.yaml", "\n".join(lines) + "\n")


def make_action(data_dir: Path, aid: str, slots: list[str], extras: dict | None = None,
                anima: str = "", moss_caption: str = "", items: list[dict] | None = None, **fields) -> Path:
    """actions/ に最小限の Action を書く（mossItems は既定で空）。"""
    lines = [f"id: {aid}", f"name: {y(aid)}", f"slots: {y(slots)}",
             f"mossCaption: {y(moss_caption)}", f"anima: {y(anima)}",
             f"mossItems: {y(items or [])}"]
    if extras is not None:
        lines.append(f"extras: {y(extras)}")
    lines += [f"{k}: {y(v)}" for k, v in fields.items()]
    return write(data_dir / "actions" / f"{aid}.yaml", "\n".join(lines) + "\n")


def scene_of(action: str, cast: dict, background: str = "beach", **fields) -> Scene:
    """cast は {slot: "character_id"} か {slot: ("character_id", "outfit")}。"""
    entries = {}
    for slot, v in cast.items():
        char, outfit = (v, None) if isinstance(v, str) else v
        entries[slot] = CastEntry(char, outfit)
    return Scene(id=fields.pop("id", "tmp_scene"), background=background, action=action, cast=entries, **fields)


def compile_sample(repo: Repository, scene_id: str, **changes):
    """サンプル Scene を（必要なら一部を差し替えて）コンパイルする。"""
    return compile_scene(repo, replace(repo.scene(scene_id), **changes))
