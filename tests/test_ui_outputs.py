"""イベント処理が、Gradio の出力検証を通る形で返していること。

空の dict `{}` を返すと、Gradio は「部品→値」の対応ではなく1個の値として数えて、
「didn't return enough output values」で落ちる（エディタでフォルダを検証したときの不具合）。更新するものが無いときは
`gr.skip()`（全部の出力を「更新なし」にする）か、少なくとも1つの部品を含む dict を返す。
"""
import ast
import asyncio
import copy
import inspect

import gradio as gr
import pytest
from gradio.blocks import SessionState

from conftest import ROOT, check_output_shape
from moa.editor.ui.app import build_editor
from moa.ui.app import build_ui

UI_FILES = {"editor": ROOT / "moa" / "editor" / "ui" / "app.py", "builder": ROOT / "moa" / "ui" / "app.py"}


# ---- 静的チェック：空の dict を返す書き方がソースに残っていないこと ----
def _branches(node):
    """return / yield の値。条件式（a if c else b）は、どの枝も調べる。"""
    if isinstance(node, ast.IfExp):
        return _branches(node.body) + _branches(node.orelse)
    return [node]


def _is_empty_dict(node) -> bool:
    return (isinstance(node, ast.Dict) and not node.keys) or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict"
        and not node.args and not node.keywords)


def empty_dict_returns(source: str) -> list[int]:
    """`return {}`、`return dict()`、`return x if c else {}`、`yield {}` の行番号。"""
    return sorted(node.lineno for node in ast.walk(ast.parse(source))
                  if isinstance(node, (ast.Return, ast.Yield)) and node.value is not None
                  and any(_is_empty_dict(b) for b in _branches(node.value)))


def test_the_checker_finds_the_shapes_that_broke_before():
    src = ("def f(a):\n    if not a:\n        return {}\n    return {1: 2} if a else {}\n"
           "def g():\n    yield dict()\n    return {'k': 1}\n")
    assert empty_dict_returns(src) == [3, 4, 6]
    assert empty_dict_returns("def f():\n    out = {}\n    return out\n") == []          # 変数経由は動的チェックで見る


@pytest.mark.parametrize("name", UI_FILES)
def test_handlers_never_return_an_empty_dict(name):
    lines = empty_dict_returns(UI_FILES[name].read_text(encoding="utf-8"))
    assert lines == [], f"{UI_FILES[name].name} の {lines} 行目が空の dict を返しています（更新なしは gr.skip()）"


# ---- 動的チェック：初期状態で全イベントを呼び、Gradio の出力検証に通す ----
def output_errors(demo) -> list[str]:
    """全イベントを部品の初期値で呼び、返り値を Gradio の出力検証（個数・部品の対応）に通す。失敗したイベントの一覧を返す。

    ジェネレータ（生成など）は最後まで回す。`@gr.render` のイベントは Blocks の外からは呼べないので対象外。
    静的チェックで見つけられない形（`out = {}` を条件付きで埋める、など）は、こちらで捕まえる。
    """
    values = {id(c): getattr(c, "value", None) for c in demo.blocks.values()}
    failed = []
    for fn in demo.fns.values():
        if getattr(fn, "renderable", None) is not None:
            continue
        out = fn.fn(*[values[id(c)] for c in fn.inputs])
        try:
            for part in (list(out) if inspect.isgenerator(out) else [out]):
                check_output_shape(demo, fn, part)
        except ValueError as e:
            failed.append(f"{fn.api_name or fn.name}: {str(e).splitlines()[0]}")
    return failed


def test_every_editor_event_returns_valid_outputs_in_the_initial_state(repo):
    assert output_errors(build_editor(repo)) == []


def test_every_builder_event_returns_valid_outputs_in_the_initial_state(repo, monkeypatch):
    monkeypatch.setattr("moa.ui.app.pick_folder", lambda *a, **k: None)   # OS のフォルダ選択ダイアログは開かない
    assert output_errors(build_ui(repo)) == []


def test_the_shape_check_agrees_with_the_real_gradio_pipeline(repo):
    """軽い個数チェック（check_output_shape）が、Gradio 本体の postprocess_data と同じ結論になること。"""
    demo = build_editor(repo)
    check = {f.api_name: f for f in demo.fns.values()}["check"]
    cases = [("空の dict", {}, False), ("gr.skip()", gr.skip(), True), ("部品を含む dict", {demo.named["issues"]: ""}, True)]
    for label, out, valid in cases:
        try:
            check_output_shape(demo, check, out)
            light = True
        except ValueError:
            light = False
        try:   # postprocess_data は渡された dict の中身を書き換えるので、コピーを渡す
            copied = {k: copy.deepcopy(v) for k, v in out.items()}
            asyncio.run(demo.postprocess_data(check, copied, SessionState(demo)))
            real = True
        except ValueError:
            real = False
        assert light == real == valid, label
