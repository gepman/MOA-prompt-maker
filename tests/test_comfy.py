"""ComfyUI 連携（13章）。実物の代わりに、偽のクライアント（スクリプト化したイベント列）と小さなサーバーを使う。"""
import io
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from conftest import ROOT, compile_sample, edit, write
from moa.comfy import (ComfyClient, ComfyError, Generation, apply, check_seed, differences, effective_gen,
                       iter_retry_anima, iter_run, load_config, new_seed, run, seed_limit, suggest)
from moa.comfy.client import EventStream
from moa.comfy.workflow import (ancestors, anima_only_workflow, anima_size, read_gen, stage_boundary,
                                suggestion_to_yaml, supports_anima_scale)
from moa.core import CastEntry, Repository, compile_scene
from moa.core.constants import SEED_MAX

FIXTURE = ROOT / "tests" / "fixtures" / "workflow.json"
CONFIG_TEXT = (ROOT / "config.yaml").read_text(encoding="utf-8")
WORKFLOW_VALUES = {"moss": {"steps": 50, "cfg": 5}, "anima": {"steps": 30, "cfg": 2, "denoise": 0.35}}


def png(color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    """tests/fixtures のワークフローを指す config.yaml（実ファイルの config.yaml と同じ割り当て）。"""
    wf = tmp_path / "workflow.json"
    wf.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_TEXT.replace("workflow/moss_to_anima_i2i_api_v1.1.json", "workflow.json"), encoding="utf-8")
    return cfg


@pytest.fixture
def cfg(cfg_path):
    c, issues = load_config(cfg_path)
    assert c is not None, issues
    return c


@pytest.fixture
def result(repo):
    r = compile_sample(repo, "beach_chat__trio")
    assert r.ok
    return r


# ---- suggest ----
def test_suggest_matches_the_hand_written_config():
    s = suggest(json.loads(FIXTURE.read_text(encoding="utf-8")))
    n = s["nodes"]
    assert (n["mossPositive"]["node"], n["mossNegative"]["node"]) == ("4", "5")
    assert (n["animaPositive"]["node"], n["animaNegative"]["node"]) == ("13", "14")
    assert [x["node"] for x in n["seed"]] == ["7", "16"]
    assert (n["width"]["node"], n["height"]["node"]) == ("6", "6")
    assert (n["samplers"]["moss"]["node"], n["samplers"]["anima"]["node"]) == ("7", "16")
    assert s["outputs"] == {"moss": ["9"], "anima": ["18"]} and s["notes"] == []
    assert 'seed:\n    - {node: "7", input: seed}' in suggestion_to_yaml(s)
    assert '  samplers:\n    moss: {node: "7"}' in suggestion_to_yaml(s)


def test_suggest_falls_back_to_structure_when_titles_are_missing():
    wf = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for node in wf.values():
        node.pop("_meta", None)
    s = suggest(wf)
    assert s["nodes"]["mossPositive"]["node"] == "4" and s["nodes"]["animaPositive"]["node"] == "13"
    assert s["outputs"] == {"moss": ["9"], "anima": ["18"]}


# ---- config ----
def test_config_loads_and_resolves_paths(cfg, tmp_path):
    assert cfg.url == "http://127.0.0.1:8188" and cfg.workflow_path == tmp_path / "workflow.json"
    assert [b.node for b in cfg.seeds] == ["7", "16"] and cfg.outputs == {"moss": ["9"], "anima": ["18"]}
    assert cfg.samplers == {"moss": "7", "anima": "16"}
    for stage, params in WORKFLOW_VALUES.items():
        assert {p: cfg.workflow_value(stage, p) for p in params} == params


def test_config_url_override_and_trailing_slash(cfg_path):
    c, _ = load_config(cfg_path, url_override="http://example:9999/")
    assert c.url == "http://example:9999"


def test_config_missing_file_is_a_warning_not_a_crash(tmp_path):
    c, issues = load_config(tmp_path / "nope.yaml")
    assert c is None and issues[0].level == "warning"


@pytest.mark.parametrize("old,new,expect", [
    ('mossPositive:  {node: "4",  input: text}', 'mossPositive:  {node: "99", input: text}', "ノード '99'"),
    ('animaPositive: {node: "13", input: text}', 'animaPositive: {node: "13", input: nope}', "入力 'nope'"),
    ('mossPositive:  {node: "4",  input: text}', 'mossPositive:  {node: "4",  input: clip}', "接続されており"),
    ('anima: ["18"]', 'anima: ["17"]', "画像保存ノードではありません"),
    ('height: {node: "6", input: height}', '', "width と nodes.height"),
    ('moss:  {node: "7"}', 'moss:  {node: "99"}', "ノード '99'"),
    ('anima: {node: "16"}', 'anima: {node: "6"}', "入力 'steps'"),
    ('anima: {node: "16"}', 'anime: {node: "16"}', "moss / anima のいずれか"),
])
def test_config_validation_against_workflow(cfg_path, old, new, expect):
    edit(cfg_path, old, new)
    c, issues = load_config(cfg_path)
    assert c is None and any(expect in i.message for i in issues if i.level == "error"), issues


def test_config_samplers_are_optional(cfg_path):
    edit(cfg_path, "  samplers:", "  _off:")
    c, _ = load_config(cfg_path)
    assert c is not None and c.samplers == {} and not c.has_sampler("moss")


def test_config_rejects_non_api_workflow(cfg_path):
    (cfg_path.parent / "workflow.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
    c, issues = load_config(cfg_path)
    assert c is None and "API形式" in issues[0].message


# ---- apply：プロンプト・シード・サイズ（Background の canvas）・ネガティブ・gen ----
def test_apply_seed_canvas_and_prompts(cfg, result):
    wf = apply(cfg.workflow, cfg, result, 12345)
    assert wf["7"]["inputs"]["seed"] == wf["16"]["inputs"]["seed"] == 12345
    assert (wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]) == (1536, 1024)
    assert wf["4"]["inputs"]["text"] == result.moss_prompt and wf["13"]["inputs"]["text"] == result.anima_prompt
    assert json.loads(wf["4"]["inputs"]["text"])["items"]


def test_apply_uses_the_scenes_background_canvas(cfg, data_dir):
    write(data_dir / "backgrounds" / "wide.yaml",
          "id: wide\nname: w\ncanvas: { w: 1920, h: 1080 }\nmossCaption: ''\nanima: ''\n")
    r = compile_sample(Repository(data_dir), "beach_chat__trio", background="wide")
    wf = apply(cfg.workflow, cfg, r, 1)
    assert (wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]) == (1920, 1080)


def test_apply_negative_off_keeps_workflow_defaults_and_on_replaces(cfg, repo):
    off = compile_sample(repo, "beach_chat__trio")
    on = compile_sample(repo, "beach_chat__trio", apply_negative=True)
    orig = json.loads(json.dumps(cfg.workflow))
    wf = apply(cfg.workflow, cfg, off, 1)
    assert wf["5"]["inputs"]["text"] == orig["5"]["inputs"]["text"] and wf["14"]["inputs"]["text"] == orig["14"]["inputs"]["text"]
    wf = apply(cfg.workflow, cfg, on, 1)
    assert wf["5"]["inputs"]["text"] == on.negative_moss and wf["14"]["inputs"]["text"] == on.negative_anima
    assert cfg.workflow == orig                                  # 元のワークフローは変更されない


def test_apply_gen_overrides_only_the_listed_items(cfg, result):
    gen = {"moss": {"steps": 20, "cfg": 3.5}, "anima": {"denoise": 0.45}}
    wf = apply(cfg.workflow, cfg, result, 1, gen)
    assert (wf["7"]["inputs"]["steps"], wf["7"]["inputs"]["cfg"]) == (20, 3.5)
    assert wf["16"]["inputs"]["denoise"] == 0.45
    assert (wf["16"]["inputs"]["steps"], wf["16"]["inputs"]["cfg"]) == (30, 2)       # 書かれなかった項目はワークフローの値
    assert wf["7"]["inputs"]["denoise"] == 1                                         # MOSS段の denoise には触れない
    assert apply(cfg.workflow, cfg, result, 1, None) == apply(cfg.workflow, cfg, result, 1, {})


def test_apply_ignores_moss_denoise_and_stages_without_sampler(cfg, result):
    assert apply(cfg.workflow, cfg, result, 1, {"moss": {"denoise": 0.9}})["7"]["inputs"]["denoise"] == 1
    del cfg.samplers["anima"]
    assert apply(cfg.workflow, cfg, result, 1, {"anima": {"steps": 5}})["16"]["inputs"]["steps"] == 30


def test_read_gen_and_effective_gen_return_every_item(cfg, result):
    wf = apply(cfg.workflow, cfg, result, 1, {"anima": {"steps": 14}})
    expected = {"moss": {"steps": 50, "cfg": 5}, "anima": {"steps": 14, "cfg": 2, "denoise": 0.35}}
    assert read_gen(wf, cfg) == expected == effective_gen(cfg, {"anima": {"steps": 14}})
    assert effective_gen(cfg, None) == WORKFLOW_VALUES


# ---- Anima 段だけのワークフロー ----
def test_stage_boundary_is_the_pixels_input_of_the_anima_encoder(cfg):
    assert stage_boundary(cfg.workflow, cfg) == ("15", "pixels")
    assert ancestors(cfg.workflow, ["9"]) == {"9", "8", "7", "6", "5", "4", "3", "2", "1"}


def test_anima_only_workflow_reads_the_moss_image_from_output(cfg, result):
    wf = apply(cfg.workflow, cfg, result, 777, {"anima": {"steps": 14}})
    only = anima_only_workflow(wf, cfg, {"filename": "moss_00005_.png", "subfolder": "", "type": "output"})
    assert sorted(only, key=int) == ["10", "11", "12", "13", "14", "15", "16", "17", "18", "19"]   # MOSS段は送らない
    assert only["19"] == {"class_type": "LoadImage", "inputs": {"image": "moss_00005_.png [output]"},
                          "_meta": {"title": "MOSS画像（Anima段の再試行）"}}
    assert only["15"]["inputs"]["pixels"] == ["19", 0]
    assert only["16"]["inputs"]["seed"] == 777 and only["16"]["inputs"]["steps"] == 14      # シード・gen は同じ
    assert only["13"]["inputs"]["text"] == result.anima_prompt
    assert "7" in wf and "9" in wf                                                           # 元は変更されない
    sub = anima_only_workflow(wf, cfg, {"filename": "m.png", "subfolder": "a/b", "type": "temp"})
    assert sub["19"]["inputs"]["image"] == "a/b/m.png [temp]"


def test_anima_only_workflow_needs_a_single_boundary(cfg, result):
    wf = apply(cfg.workflow, cfg, result, 1)
    wf["16"]["inputs"]["pixels"] = ["8", 0]                        # Anima 段が MOSS 段から受け取る接続が2つになる
    wf["15"]["inputs"]["pixels"] = ["8", 0]
    with pytest.raises(ComfyError, match="1つに決まる"):
        stage_boundary(wf, cfg)


# ---- シード（13.2章）----
def test_new_seed_is_random_and_within_the_range():
    seeds = {new_seed() for _ in range(50)}
    assert len(seeds) > 40 and all(0 <= s <= SEED_MAX for s in seeds)
    assert all(0 <= new_seed(10) <= 10 for _ in range(50))


def test_check_seed_rejects_out_of_range():
    check_seed(0, 10)
    check_seed(10, 10)
    for bad in (-1, 11):
        with pytest.raises(ComfyError, match="範囲外"):
            check_seed(bad, 10)


# ---- 偽のクライアント：スクリプト化したイベント列 ----
def ev(kind, **data):
    return {"type": kind, "data": {"prompt_id": "p1", **data}}


def executed(node, filename, sub="", type_="output"):
    return ev("executed", node=node, output={"images": [{"filename": filename, "subfolder": sub, "type": type_}]})


START, DONE = ev("execution_start"), ev("executing", node=None)
MOSS_OK = [START, ev("progress", value=10, max=50), executed("9", "moss_00001_.png")]
ANIMA_OK = [ev("progress", value=5, max=14), executed("18", "anima_00001_.png", "sub"), DONE]


class ScriptedStream:
    def __init__(self, script):
        self.script = script

    def get(self, timeout):
        item = self.script.pop(0) if self.script else None
        if item is None:
            time.sleep(0.03)                                       # 時間を進める（タイムアウトのテスト用）
            return None
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient(ComfyClient):
    def __init__(self, script, seed_max=None):
        super().__init__("http://fake.invalid", timeout_sec=5)
        self.script, self.seed_max = list(script), seed_max
        self.submitted, self.fetched, self.interrupted, self.cancelled = [], [], 0, []

    def submit(self, workflow):
        self.submitted.append(workflow)
        return "p1"

    def fetch_image(self, filename, subfolder="", type_="output"):
        self.fetched.append((filename, subfolder, type_))
        return png("red" if filename.startswith("moss") else "blue")

    def interrupt(self):
        self.interrupted += 1
        self.script.insert(0, ev("execution_interrupted"))         # ComfyUI のふるまいを真似る

    def cancel_queued(self, prompt_id):
        self.cancelled.append(prompt_id)

    def input_max(self, class_type, input_name):
        return self.seed_max

    @contextmanager
    def events(self):
        yield ScriptedStream(self.script)


def gen_of(events) -> Generation:
    last = list(events)[-1]
    assert isinstance(last, Generation)
    return last


def messages(events):
    return [e for e in events if isinstance(e, str)]


def test_normal_run_sends_one_prompt_and_collects_both_stages(cfg, result):
    fc = FakeClient(MOSS_OK + ANIMA_OK)
    events = list(iter_run(cfg, result, client=fc, seed=777))
    g = events[-1]
    assert len(fc.submitted) == 1                                   # 通常は1プロンプト
    sent = fc.submitted[0]
    assert sent["7"]["inputs"]["seed"] == sent["16"]["inputs"]["seed"] == 777 and "9" in sent and "18" in sent
    assert g.complete and g.failed_stage is None and g.error is None and not g.partial
    assert [(i.stage, i.filename) for i in g.images] == [("moss", "moss_00001_.png"), ("anima", "anima_00001_.png")]
    assert [i.record_path for i in g.images] == ["output/moss_00001_.png", "output/sub/anima_00001_.png"]
    assert Image.open(io.BytesIO(g.images[0].data)).getpixel((0, 0)) == (255, 0, 0)
    assert g.gen == WORKFLOW_VALUES and g.seed == 777
    assert (g.background, g.action, g.canvas) == ("beach", "beach_chat", {"w": 1536, "h": 1024})
    msgs = messages(events)
    assert msgs[0] == "シード 777 で送信中…" and "MOSS段が完了しました" in msgs and "Anima段：5/14 ステップ" in msgs
    rec = g.record()
    assert (rec.seed, rec.images, rec.partial, rec.gen) == (777, [i.record_path for i in g.images], False, WORKFLOW_VALUES)


def test_run_helper_returns_the_final_generation(cfg, result):
    assert run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1).complete


def test_events_for_other_prompts_are_ignored(cfg, result):
    other = {"type": "executed", "data": {"prompt_id": "someone_else", "node": "9", "output": {"images": [
        {"filename": "STRANGER.png", "subfolder": "", "type": "output"}]}}}
    g = gen_of(iter_run(cfg, result, client=FakeClient([other, ev("status")] + MOSS_OK + [{"type": "binary"}] + ANIMA_OK), seed=1))
    assert g.complete and all("STRANGER" not in i.filename for i in g.images)


def test_a_random_seed_is_drawn_per_generation_within_the_workflow_limit(cfg, result):
    seeds = set()
    for _ in range(5):
        fc = FakeClient(MOSS_OK + ANIMA_OK, seed_max=1000)
        g = gen_of(iter_run(cfg, result, client=fc))
        assert 0 <= g.seed <= 1000 and fc.submitted[0]["7"]["inputs"]["seed"] == g.seed
        seeds.add(g.seed)
    assert len(seeds) > 1
    assert seed_limit(cfg, FakeClient([], seed_max=1000)) == 1000
    assert seed_limit(cfg, FakeClient([], seed_max=2**64 - 1)) == SEED_MAX      # SPEC の上限が実効になる
    assert seed_limit(cfg, FakeClient([], seed_max=None)) == SEED_MAX           # /object_info が取れなくても動く


def test_an_out_of_range_seed_stops_before_sending(cfg, result):
    fc = FakeClient(MOSS_OK + ANIMA_OK, seed_max=1000)
    with pytest.raises(ComfyError, match="範囲外"):
        list(iter_run(cfg, result, client=fc, seed=1001))
    assert fc.submitted == []


def test_moss_stage_failure_returns_no_images(cfg, result):
    fc = FakeClient([START, ev("execution_error", node_id="7", node_type="KSampler", exception_message="CUDA out of memory")])
    g = gen_of(iter_run(cfg, result, client=fc, seed=5))
    assert g.failed_stage == "moss" and g.images == [] and not g.complete and not g.partial
    assert "MOSS段" in g.error and "CUDA out of memory" in g.error and "KSampler" in g.error


def test_anima_stage_failure_keeps_the_moss_image(cfg, result):
    fc = FakeClient(MOSS_OK + [ev("execution_error", node_id="16", node_type="KSampler", exception_message="boom")])
    g = gen_of(iter_run(cfg, result, client=fc, seed=5))
    assert g.failed_stage == "anima" and "Anima段" in g.error and "boom" in g.error
    assert [i.stage for i in g.images] == ["moss"] and g.partial and not g.complete
    assert g.record().partial is True and g.record().images == ["output/moss_00001_.png"]
    snap = g.snapshot()
    assert snap["failed_stage"] == "anima" and snap["seed"] == 5
    assert snap["moss_ref"] == {"filename": "moss_00001_.png", "subfolder": "", "type": "output",
                                "record_path": "output/moss_00001_.png"}
    assert json.dumps(snap)                                                               # バイト列を含まず、JSON にできる


def test_an_interrupt_is_a_failure_of_the_current_stage(cfg, result):
    g = gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + [ev("execution_interrupted")]), seed=5))
    assert g.failed_stage == "anima" and "中断" in g.error
    g = gen_of(iter_run(cfg, result, client=FakeClient([START, ev("execution_interrupted")]), seed=5))
    assert g.failed_stage == "moss"


def test_timeout_is_per_stage_and_interrupts_the_job(cfg, result):
    cfg.timeout_sec = 0.3
    fc = FakeClient([START])                                        # MOSS 段が終わらない
    g = gen_of(iter_run(cfg, result, client=fc, seed=5))
    assert g.failed_stage == "moss" and "タイムアウト" in g.error and fc.interrupted == 1 and g.images == []
    fc = FakeClient(MOSS_OK)                                        # MOSS 段は終わるが、Anima 段が終わらない
    g = gen_of(iter_run(cfg, result, client=fc, seed=5))
    assert g.failed_stage == "anima" and "Anima段" in g.error and fc.interrupted == 1
    assert g.partial and len(g.images) == 1                         # MOSS 画像は残る


def test_each_stage_gets_its_own_full_timeout(cfg, result):
    """MOSS 段に timeout の大半を使っても、Anima 段のタイマーは MOSS 完了後に改めて数え始める。"""
    cfg.timeout_sec = 0.5
    script = [START] + [None] * 10 + [executed("9", "moss_00001_.png")] + [None] * 10 + ANIMA_OK   # 各約0.3秒
    assert gen_of(iter_run(cfg, result, client=FakeClient(script), seed=1)).complete             # 合計 0.6秒 > 0.5秒 でも成功


def test_waiting_in_the_queue_does_not_count_against_a_stage(cfg, result):
    cfg.timeout_sec = 0.5
    script = [None] * 12 + MOSS_OK + ANIMA_OK                      # 実行開始まで約0.36秒待つ
    assert gen_of(iter_run(cfg, result, client=FakeClient(script), seed=1)).complete


def test_never_starting_is_an_error_and_cancels_the_queued_job(cfg, result):
    cfg.timeout_sec = 0.2
    fc = FakeClient([])
    with pytest.raises(ComfyError, match="実行が始まりませんでした"):
        list(iter_run(cfg, result, client=fc, seed=1))
    assert fc.cancelled == ["p1"]


def test_finishing_without_the_anima_image_is_an_anima_failure(cfg, result):
    g = gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + [DONE]), seed=1))
    assert g.failed_stage == "anima" and "画像が出力に含まれていません" in g.error and g.partial
    g = gen_of(iter_run(cfg, result, client=FakeClient([START, DONE]), seed=1))
    assert g.failed_stage == "moss" and g.images == []


def test_a_dropped_websocket_fails_the_current_stage(cfg, result):
    g = gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + [ComfyError("ComfyUI との WebSocket 接続が切れました")]), seed=1))
    assert g.failed_stage == "anima" and "接続が切れました" in g.error and g.partial


def test_copy_dir_saves_images_and_records_the_copies_even_for_a_partial_result(cfg, result, tmp_path):
    dest = tmp_path / "my images"
    fc = FakeClient(MOSS_OK + [ev("execution_error", node_id="16", exception_message="x")])
    g = gen_of(iter_run(cfg, result, copy_dir=dest, client=fc, seed=1))
    assert g.partial and Path(g.images[0].record_path) == dest / "moss_00001_.png"
    assert (dest / "moss_00001_.png").read_bytes() == g.images[0].data
    g2 = gen_of(iter_run(cfg, result, copy_dir=dest, client=FakeClient(MOSS_OK + ANIMA_OK), seed=2))
    assert Path(g2.images[0].record_path).name == "moss_00001__1.png"                       # 同名は上書きしない
    assert [Path(i.record_path).parent for i in g2.images] == [dest, dest]


def test_output_dir_makes_absolute_record_paths(cfg, result, tmp_path):
    cfg.output_dir = tmp_path / "ComfyUI" / "output"
    g = gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1))
    assert g.images[1].record_path == str(tmp_path / "ComfyUI" / "output" / "sub" / "anima_00001_.png")


def test_refuses_when_the_scene_has_errors(cfg, repo):
    scene = repo.scene("beach_chat__trio")
    bad = compile_scene(repo, replace(scene, cast={k: v for k, v in scene.cast.items() if k != "right"}))
    fc = FakeClient([])
    with pytest.raises(ComfyError, match="検証エラー"):
        list(iter_run(cfg, bad, client=fc))
    assert fc.submitted == []


def test_canvas_not_a_multiple_of_16_warns(cfg, data_dir):
    write(data_dir / "backgrounds" / "odd.yaml", "id: odd\nname: o\ncanvas: { w: 1000, h: 1000 }\nmossCaption: ''\nanima: ''\n")
    r = compile_sample(Repository(data_dir), "beach_chat__trio", background="odd")
    assert any("16の倍数" in m for m in messages(iter_run(cfg, r, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)))


# ---- Anima 段だけの再試行（13.4章）----
def failed_first_run(cfg, result, gen=None, seed=777):
    fc = FakeClient(MOSS_OK + [ev("execution_error", node_id="16", exception_message="boom")])
    return gen_of(iter_run(cfg, result, client=fc, seed=seed, gen=gen)).snapshot()


def test_retry_resends_only_the_anima_stage_with_the_same_seed_and_gen(cfg, result):
    gen = {"anima": {"steps": 14, "denoise": 0.45}}
    previous = failed_first_run(cfg, result, gen)
    fc = FakeClient(ANIMA_OK)
    events = list(iter_retry_anima(cfg, result, previous, client=fc, gen=gen))
    g = events[-1]
    assert len(fc.submitted) == 1
    sent = fc.submitted[0]
    assert "7" not in sent and "9" not in sent and sent["19"]["inputs"]["image"] == "moss_00001_.png [output]"   # MOSS 段は再実行しない
    assert sent["16"]["inputs"]["seed"] == 777 and sent["16"]["inputs"]["steps"] == 14      # シード・gen は最初の生成と同じ
    assert g.complete and g.seed == 777 and not g.partial
    assert [(i.stage, i.record_path) for i in g.images] == [("moss", "output/moss_00001_.png"), ("anima", "output/sub/anima_00001_.png")]
    assert g.gen == previous["gen"]
    assert fc.fetched[0] == ("moss_00001_.png", "", "output")                                 # 表示用に MOSS 画像を取り直す
    assert any("Anima 段だけを再送" in m for m in messages(events))


def test_retry_can_fail_again_and_keeps_the_moss_image(cfg, result):
    previous = failed_first_run(cfg, result)
    fc = FakeClient([START, ev("execution_error", node_id="16", exception_message="again")])
    g = gen_of(iter_retry_anima(cfg, result, previous, client=fc))
    assert g.failed_stage == "anima" and g.partial and "again" in g.error and [i.stage for i in g.images] == ["moss"]


def test_retry_only_applies_to_an_anima_failure_with_a_moss_image(cfg, result):
    ok = gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)).snapshot()
    moss_failed = gen_of(iter_run(cfg, result, client=FakeClient([START, ev("execution_error")]), seed=1)).snapshot()
    for previous in (ok, moss_failed):
        with pytest.raises(ComfyError, match="再試行できるのは"):
            list(iter_retry_anima(cfg, result, previous, client=FakeClient([])))


def test_retry_with_an_out_of_range_seed_does_not_start(cfg, result):
    previous = failed_first_run(cfg, result, seed=5000)
    fc = FakeClient(ANIMA_OK, seed_max=1000)
    with pytest.raises(ComfyError, match="範囲外"):
        list(iter_retry_anima(cfg, result, previous, client=fc))
    assert fc.submitted == []


# ---- Anima段の解像度倍率（画面だけの設定。ImageScale を送信時に挟む）----
def test_anima_size_rounds_to_16_and_has_a_floor():
    assert anima_size({"w": 1024, "h": 1536}, 1.3) == (1328, 2000)   # 1331.2 → 1328、1996.8 → 2000
    assert anima_size({"w": 1536, "h": 1024}, 1.0) == (1536, 1024)
    assert anima_size({"w": 1024, "h": 1024}, 2.0) == (2048, 2048)
    assert anima_size({"w": 1024, "h": 1536}, 0.1) == (96, 160)       # 102.4 → 96、153.6 → 160
    assert anima_size({"w": 320, "h": 320}, 0.1) == (64, 64)          # 下限


def test_apply_without_scale_adds_no_node(cfg, result):
    assert apply(cfg.workflow, cfg, result, 1, None, 1.0) == apply(cfg.workflow, cfg, result, 1)


def test_apply_with_scale_inserts_an_image_scale_before_the_anima_stage(cfg, result):
    before = json.dumps(cfg.workflow, sort_keys=True)
    wf = apply(cfg.workflow, cfg, result, 1, None, 1.5)
    assert json.dumps(cfg.workflow, sort_keys=True) == before                      # 元は変更しない
    new = [k for k in wf if k not in cfg.workflow]
    assert len(new) == 1 and wf[new[0]]["class_type"] == "ImageScale"
    scale = wf[new[0]]["inputs"]
    assert (scale["width"], scale["height"], scale["crop"]) == (2304, 1536, "disabled")   # 1536×1024 ×1.5
    assert scale["image"] == cfg.workflow["15"]["inputs"]["pixels"]                # MOSS のデコード結果を受ける
    assert wf["15"]["inputs"]["pixels"] == [new[0], 0]                             # VAEEncode はその出力を受ける
    assert (wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]) == (1536, 1024)  # MOSS段の canvas はそのまま


def test_scale_is_kept_in_the_anima_only_retry_workflow(cfg, result):
    wf = apply(cfg.workflow, cfg, result, 1, None, 0.5)
    retry = anima_only_workflow(wf, cfg, {"filename": "m.png", "subfolder": "", "type": "output"})
    scale_id = retry["15"]["inputs"]["pixels"][0]
    assert retry[scale_id]["class_type"] == "ImageScale" and (retry[scale_id]["inputs"]["width"], retry[scale_id]["inputs"]["height"]) == (768, 512)
    assert retry[retry[scale_id]["inputs"]["image"][0]]["class_type"] == "LoadImage"
    assert "7" not in retry and "9" not in retry                                   # MOSS 段は含まない


def test_supports_anima_scale_needs_canvas_binding(cfg, cfg_path):
    assert supports_anima_scale(cfg)
    edit(cfg_path, 'width:  {node: "6", input: width}', "")
    edit(cfg_path, 'height: {node: "6", input: height}', "")
    c, _ = load_config(cfg_path)
    assert c is not None and not supports_anima_scale(c)


def test_iter_run_sends_the_scale_and_reports_the_size(cfg, result):
    fc = FakeClient(MOSS_OK + ANIMA_OK)
    events = list(iter_run(cfg, result, client=fc, seed=1, anima_scale=1.5))
    g = events[-1]
    assert g.anima_size == (2304, 1536) and any(n["class_type"] == "ImageScale" for n in fc.submitted[0].values())
    assert any("2304×1536" in m for m in messages(events))
    assert g.gen == gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)).gen   # 記録される gen は倍率に影響されない
    plain = gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1))
    assert plain.anima_size is None


def test_small_scale_warns(cfg, result):
    msgs = messages(iter_run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1, anima_scale=0.2))
    assert any("512px 未満" in m for m in msgs)


def test_retry_uses_the_current_scale(cfg, result):
    previous = failed_first_run(cfg, result)
    fc = FakeClient(ANIMA_OK)
    g = gen_of(iter_retry_anima(cfg, result, previous, client=fc, anima_scale=0.5))
    assert g.anima_size == (768, 512)
    assert any(n["class_type"] == "ImageScale" for n in fc.submitted[0].values())


# ---- 記録条件（13.4章）----
@pytest.fixture
def snap(cfg, result):
    return gen_of(iter_run(cfg, result, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)).snapshot()


def test_differences_is_empty_when_nothing_changed(cfg, result, snap):
    assert differences(snap, result, effective_gen(cfg, None)) == []


def test_differences_names_each_changed_item(cfg, repo, snap):
    scene = repo.scene("beach_chat__trio")
    swapped = replace(scene, cast={**scene.cast, "center": CastEntry("skyla_bw", "none")})
    assert differences(snap, compile_scene(repo, swapped), effective_gen(cfg, None)) == ["mossPrompt", "animaPrompt"]
    assert differences(snap, compile_scene(repo, replace(scene, apply_negative=True)), effective_gen(cfg, None)) == ["ネガティブ適用"]


def test_differences_background_action_canvas_and_gen(cfg, repo, data_dir, snap):
    write(data_dir / "backgrounds" / "wide.yaml", "id: wide\nname: w\ncanvas: { w: 1920, h: 1080 }\nmossCaption: ''\nanima: ''\n")
    r = compile_sample(Repository(data_dir), "beach_chat__trio", background="wide")
    d = differences(snap, r, effective_gen(cfg, None))
    assert "Background" in d and "canvas" in d and "Action" not in d

    assert "Action" in differences(snap, compile_sample(repo, "beach_solo__elesa"), effective_gen(cfg, None))

    same = compile_sample(repo, "beach_chat__trio")
    assert differences(snap, same, effective_gen(cfg, {"moss": {"cfg": 3.0}, "anima": {"denoise": 0.45}})) == \
        ["gen.moss.cfg", "gen.anima.denoise"]
    assert differences(snap, same, effective_gen(cfg, {"anima": {"steps": 30}})) == []     # 実効値が同じなら一致
    assert differences(snap, same, None) == []                                              # samplers なしの設定では gen は比べない


def test_differences_compares_negatives_only_when_applied(cfg, repo, data_dir):
    on = compile_sample(repo, "beach_chat__trio", apply_negative=True)
    snap_on = gen_of(iter_run(cfg, on, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)).snapshot()
    assert differences(snap_on, on, effective_gen(cfg, None)) == []
    edit(data_dir / "actions" / "beach_chat.yaml", "negativeAnima: |\n  duplicate", "negativeAnima: |\n  duplicate, extra_bad")
    changed = Repository(data_dir)
    assert "ネガティブ（Anima）" in differences(snap_on, compile_sample(changed, "beach_chat__trio", apply_negative=True),
                                                  effective_gen(cfg, None))

    off = compile_sample(repo, "beach_chat__trio")
    snap_off = gen_of(iter_run(cfg, off, client=FakeClient(MOSS_OK + ANIMA_OK), seed=1)).snapshot()
    assert differences(snap_off, compile_sample(changed, "beach_chat__trio"), effective_gen(cfg, None)) == []   # OFF なら無関係


# ---- HTTP アダプタ（小さな偽サーバー）----
class FakeHttp:
    def __init__(self, reject=None, seed_max=18446744073709551615):
        self.reject, self.seed_max = reject, seed_max
        self.posts, fake = [], self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): ...

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fake.posts.append((self.path, body))
                if self.path == "/prompt":
                    return self._json(fake.reject, 400) if fake.reject else self._json({"prompt_id": "p1", "node_errors": {}})
                self._json({})

            def do_GET(self):
                if self.path.startswith("/object_info/KSampler"):
                    return self._json({"KSampler": {"input": {"required": {"seed": ["INT", {"min": 0, "max": fake.seed_max}]}}}})
                if self.path.startswith("/view"):
                    data = png()
                    self.send_response(200 if "ok.png" in self.path else 404)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(404)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def http():
    servers = []

    def make(**kw):
        s = FakeHttp(**kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def test_http_submit_fetch_and_object_info(http):
    s = http()
    c = ComfyClient(s.url)
    assert c.submit({"1": {"class_type": "X", "inputs": {}}}) == "p1"
    path, body = s.posts[0]
    assert path == "/prompt" and body["client_id"] == c.client_id and "1" in body["prompt"]
    assert c.fetch_image("ok.png", "sub", "output").startswith(b"\x89PNG")
    with pytest.raises(ComfyError, match="取得できません"):
        c.fetch_image("missing.png")
    assert c.input_max("KSampler", "seed") == 18446744073709551615
    assert c.input_max("KSampler", "nope") is None and c.input_max("Nothing", "seed") is None


def test_http_rejection_is_formatted_with_node_errors(http):
    s = http(reject={"error": {"message": "Prompt outputs failed validation", "details": ""},
                     "node_errors": {"4": {"class_type": "CLIPTextEncode",
                                           "errors": [{"message": "Required input is missing", "details": "clip"}]}}})
    with pytest.raises(ComfyError) as e:
        ComfyClient(s.url).submit({})
    assert "ノード 4（CLIPTextEncode）" in str(e.value) and "Required input is missing" in str(e.value)


def test_http_interrupt_and_cancel_are_sent_and_never_raise(http):
    s = http()
    c = ComfyClient(s.url)
    c.interrupt()
    c.cancel_queued("p9")
    assert ("/interrupt", {}) in s.posts and ("/queue", {"delete": ["p9"]}) in s.posts
    dead = ComfyClient("http://127.0.0.1:1")
    dead.interrupt()
    dead.cancel_queued("p")                                         # 接続できなくても例外にしない
    assert dead.input_max("KSampler", "seed") is None


def test_connection_refused_is_friendly():
    with pytest.raises(ComfyError, match="接続できません"):
        ComfyClient("http://127.0.0.1:1").submit({})


# ---- WebSocket アダプタ（websockets の同期サーバー）----
def ws_server(handler):
    from websockets.sync.server import serve
    server = serve(handler, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.socket.getsockname()[1]}"


def test_websocket_events_are_parsed_and_binary_frames_ignored():
    seen = {}

    def handler(ws):
        seen["path"] = ws.request.path
        ws.send(json.dumps({"type": "status", "data": {}}))
        ws.send(b"\x00\x00\x00\x01preview-bytes")
        ws.send(json.dumps(ev("execution_start")))
        time.sleep(0.5)

    server, url = ws_server(handler)
    try:
        c = ComfyClient(url)
        with c.events() as stream:
            assert isinstance(stream, EventStream)
            assert stream.get(2) == {"type": "status", "data": {}}
            assert stream.get(2) == {"type": "binary"}
            assert stream.get(2)["type"] == "execution_start"
            assert stream.get(0.1) is None                           # 何も来なければ None
        assert seen["path"] == f"/ws?clientId={c.client_id}"        # 自分の clientId で購読する
    finally:
        server.shutdown()


def test_websocket_closed_by_the_server_raises_a_friendly_error():
    server, url = ws_server(lambda ws: ws.close())
    try:
        with ComfyClient(url).events() as stream:
            with pytest.raises(ComfyError, match="接続が切れました"):
                for _ in range(20):
                    stream.get(0.5)
    finally:
        server.shutdown()


def test_websocket_connect_failure_is_friendly():
    with pytest.raises(ComfyError, match="WebSocket"):
        with ComfyClient("http://127.0.0.1:1").events():
            pass


def test_full_generation_over_real_http_and_websocket(cfg, result):
    """HTTP（/prompt, /view, /object_info）と WebSocket を、実際のソケットで通す。"""
    fake = FakeHttp()

    def handler(ws):
        for e in [START, executed("9", "ok.png"), executed("18", "ok.png", "sub"), DONE]:
            ws.send(json.dumps(e))
            time.sleep(0.02)
        time.sleep(0.3)

    server, ws_url = ws_server(handler)
    try:
        client = ComfyClient(fake.url)
        client.events = ComfyClient(ws_url).events                  # WebSocket 側だけ別サーバー（テスト用）
        g = gen_of(iter_run(cfg, result, client=client, seed=42))
        assert g.complete and [i.stage for i in g.images] == ["moss", "anima"]
        sent = next(body for path, body in fake.posts if path == "/prompt")["prompt"]
        assert sent["7"]["inputs"]["seed"] == sent["16"]["inputs"]["seed"] == 42
    finally:
        server.shutdown()
        fake.close()
