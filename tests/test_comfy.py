"""ComfyUI連携。実際のComfyUIの代わりに、スレッドで立てた偽サーバーを相手に試す。"""
import io
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from conftest import ROOT, edit
from moa.comfy import ComfyClient, ComfyError, apply, iter_run, load_config, new_seed, run, suggest
from moa.comfy.workflow import suggestion_to_yaml
from moa.core import compile_scene

FIXTURE = ROOT / "tests" / "fixtures" / "workflow.json"
CONFIG_TEXT = (ROOT / "config.yaml").read_text(encoding="utf-8")


def png(color) -> bytes:
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
def result(repo, trio):
    r = compile_scene(repo, trio)
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
    assert s["outputs"] == {"moss": ["9"], "anima": ["18"]} and s["notes"] == []
    assert 'seed:\n    - {node: "7", input: seed}' in suggestion_to_yaml(s)


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
    ('  height: {node: "6", input: height}'.strip(), '', "width と nodes.height"),
])
def test_config_validation_against_workflow(cfg_path, old, new, expect):
    edit(cfg_path, old, new)
    c, issues = load_config(cfg_path)
    assert c is None and any(expect in i.message for i in issues if i.level == "error"), issues


def test_config_rejects_non_api_workflow(cfg_path):
    (cfg_path.parent / "workflow.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
    c, issues = load_config(cfg_path)
    assert c is None and "API形式" in issues[0].message


# ---- apply ----
def test_apply_same_seed_everywhere_and_canvas(cfg, result):
    wf = apply(cfg.workflow, cfg, result, 12345)
    assert wf["7"]["inputs"]["seed"] == wf["16"]["inputs"]["seed"] == 12345
    assert (wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]) == (1536, 1024)
    assert wf["4"]["inputs"]["text"] == result.moss_prompt
    assert wf["13"]["inputs"]["text"] == result.anima_prompt
    assert json.loads(wf["4"]["inputs"]["text"])["items"]  # 波括弧を含むJSONがそのまま入る


def test_apply_negative_off_keeps_workflow_defaults_and_on_replaces(cfg, result):
    orig = json.loads(json.dumps(cfg.workflow))
    off = apply(cfg.workflow, cfg, result, 1)
    assert off["5"]["inputs"]["text"] == orig["5"]["inputs"]["text"]
    assert off["14"]["inputs"]["text"] == orig["14"]["inputs"]["text"]

    on = apply(cfg.workflow, cfg, replace(result, apply_negative=True), 1)
    assert on["5"]["inputs"]["text"] == result.negative_moss
    assert on["14"]["inputs"]["text"] == result.negative_anima
    assert cfg.workflow == orig  # 元のワークフローは変更されない


def test_new_seed_is_random_and_json_safe():
    seeds = {new_seed() for _ in range(50)}
    assert len(seeds) > 40 and all(0 <= s < 2**53 for s in seeds)


# ---- 偽のComfyUI ----
class FakeComfy:
    def __init__(self, polls_before_done=2, reject=None, never_done=False, error=False, skip_stage=None):
        self.polls_before_done, self.reject, self.never_done = polls_before_done, reject, never_done
        self.error, self.skip_stage = error, skip_stage
        self.submitted: list[dict] = []
        self.polls = 0
        fake = self

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
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if fake.reject:
                    return self._json(fake.reject, 400)
                fake.submitted.append(body["prompt"])
                self._json({"prompt_id": "p1", "number": 1, "node_errors": {}})

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/history/p1":
                    fake.polls += 1
                    if fake.never_done or fake.polls <= fake.polls_before_done:
                        return self._json({})
                    if fake.error:
                        return self._json({"p1": {"outputs": {}, "status": {
                            "status_str": "error", "completed": False,
                            "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}}})
                    outs = {"9": {"images": [{"filename": "moss_stage1_00001_.png", "subfolder": "", "type": "output"}]},
                            "18": {"images": [{"filename": "anima_i2i_00001_.png", "subfolder": "sub", "type": "output"}]}}
                    if fake.skip_stage:
                        outs.pop(fake.skip_stage)
                    return self._json({"p1": {"outputs": outs, "status": {"status_str": "success", "completed": True}}})
                if u.path == "/queue":
                    return self._json({"queue_running": [], "queue_pending": [[1, "p1", {}]]})
                if u.path == "/view":
                    name = parse_qs(u.query)["filename"][0]
                    data = png("red" if name.startswith("moss") else "blue")
                    self.send_response(200)
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
def fake():
    servers = []

    def make(**kw):
        s = FakeComfy(**kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def client_for(server, cfg, timeout=5):
    return ComfyClient(server.url, timeout, poll_interval=0.01)


def test_generate_end_to_end_records_both_stages(fake, cfg, result):
    server = fake()
    msgs = []
    gen = run(cfg, result, client=client_for(server, cfg), seed=777, progress=msgs.append)

    assert gen.seed == 777 and [i.stage for i in gen.images] == ["moss", "anima"]
    # 送信されたワークフローは両サンプラーで同じシード
    sent = server.submitted[0]
    assert sent["7"]["inputs"]["seed"] == sent["16"]["inputs"]["seed"] == 777
    # 既定では ComfyUI 側のパスを記録（subfolder も反映）
    assert [i.record_path for i in gen.images] == ["output/moss_stage1_00001_.png", "output/sub/anima_i2i_00001_.png"]
    assert Image.open(io.BytesIO(gen.images[0].data)).getpixel((0, 0)) == (255, 0, 0)
    assert any("キュー待ち" in m for m in msgs) and msgs[0].startswith("シード 777")
    assert gen.record().images == [i.record_path for i in gen.images]


def test_generate_uses_random_seed_when_not_given(fake, cfg, result):
    s1, s2 = fake(), fake()
    g1 = run(cfg, result, client=client_for(s1, cfg))
    g2 = run(cfg, result, client=client_for(s2, cfg))
    assert g1.seed != g2.seed
    assert s1.submitted[0]["7"]["inputs"]["seed"] == g1.seed == s1.submitted[0]["16"]["inputs"]["seed"]


def test_generate_copies_to_explicit_folder_and_records_copy_paths(fake, cfg, result, tmp_path):
    server = fake()
    dest = tmp_path / "my images"
    gen = run(cfg, result, copy_dir=dest, client=client_for(server, cfg), seed=1)
    assert [Path(i.record_path) for i in gen.images] == [dest / "moss_stage1_00001_.png", dest / "anima_i2i_00001_.png"]
    assert all(Path(i.record_path).read_bytes() == i.data for i in gen.images)

    # 同名ファイルがあっても上書きしない
    gen2 = run(cfg, result, copy_dir=dest, client=client_for(fake(), cfg), seed=2)
    assert Path(gen2.images[0].record_path).name == "moss_stage1_00001__1.png"
    assert (dest / "moss_stage1_00001_.png").exists()


def test_generate_output_dir_makes_absolute_record_paths(fake, cfg, result, tmp_path):
    cfg.output_dir = tmp_path / "ComfyUI" / "output"
    gen = run(cfg, result, client=client_for(fake(), cfg), seed=1)
    assert gen.images[1].record_path == str(tmp_path / "ComfyUI" / "output" / "sub" / "anima_i2i_00001_.png")


def test_generate_reports_node_errors(fake, cfg, result):
    server = fake(reject={"error": {"message": "Prompt outputs failed validation", "details": ""},
                          "node_errors": {"4": {"class_type": "CLIPTextEncode",
                                                "errors": [{"message": "Required input is missing", "details": "clip"}]}}})
    with pytest.raises(ComfyError) as e:
        run(cfg, result, client=client_for(server, cfg))
    assert "ノード 4（CLIPTextEncode）" in str(e.value) and "Required input is missing" in str(e.value)


def test_generate_reports_execution_error(fake, cfg, result):
    with pytest.raises(ComfyError, match="CUDA out of memory"):
        run(cfg, result, client=client_for(fake(error=True), cfg))


def test_generate_times_out(fake, cfg, result):
    with pytest.raises(ComfyError, match="待っても完了しませんでした"):
        run(cfg, result, client=client_for(fake(never_done=True), cfg, timeout=0.2))


def test_generate_reports_missing_stage_output(fake, cfg, result):
    with pytest.raises(ComfyError, match="Anima段の画像が出力に含まれていません"):
        run(cfg, result, client=client_for(fake(skip_stage="18"), cfg))


def test_generate_connection_refused_is_friendly(cfg, result):
    with pytest.raises(ComfyError, match="接続できません"):
        run(cfg, result, client=ComfyClient("http://127.0.0.1:1", 2, poll_interval=0.01))


def test_generate_refuses_when_scene_has_errors(cfg, repo, trio):
    bad = compile_scene(repo, replace(trio, cast={"left": "skyla_bw"}))
    with pytest.raises(ComfyError, match="検証エラー"):
        list(iter_run(cfg, bad))
