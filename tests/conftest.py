import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moa.core import Repository, Scene  # noqa: E402

SAMPLES = ROOT / "spec" / "samples"
EXAMPLES = SAMPLES / "examples"


@pytest.fixture
def data_dir(tmp_path) -> Path:
    """samples/data を tmp にコピーしたもの（テスト中に自由に編集してよい）。"""
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
