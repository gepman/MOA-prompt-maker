"""ComfyUI連携の設定（config.yaml）の読み込みと、ワークフローとの突き合わせ検証。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from moa.core.models import Issue, has_error

DEFAULT_URL = "http://127.0.0.1:8188"
STAGES = ("moss", "anima")
STAGE_LABEL = {"moss": "MOSS段", "anima": "Anima段"}


@dataclass(frozen=True)
class Binding:
    """ワークフロー中の `node` の入力 `input` に値を差し込む指定。"""
    node: str
    input: str


@dataclass
class ComfyConfig:
    url: str
    workflow_path: Path
    workflow: dict
    timeout_sec: float
    output_dir: Path | None
    moss_positive: Binding
    anima_positive: Binding
    seeds: list[Binding]
    outputs: dict[str, list[str]]  # stage -> SaveImage系ノードid
    moss_negative: Binding | None = None
    anima_negative: Binding | None = None
    width: Binding | None = None
    height: Binding | None = None
    base_dir: Path = field(default_factory=Path)


def _binding(raw, label: str, issues: list[Issue], path: Path) -> Binding | None:
    if not isinstance(raw, dict) or "node" not in raw or "input" not in raw:
        issues.append(Issue("error", f"nodes.{label} は {{node: \"4\", input: text}} の形で指定してください", path))
        return None
    return Binding(str(raw["node"]), str(raw["input"]))


def _check_binding(wf: dict, b: Binding, label: str, issues: list[Issue], path: Path) -> None:
    node = wf.get(b.node)
    if node is None:
        issues.append(Issue("error", f"nodes.{label}: ノード '{b.node}' がワークフローにありません", path))
        return
    inputs = node.get("inputs", {})
    if b.input not in inputs:
        issues.append(Issue("error", f"nodes.{label}: ノード '{b.node}'（{node.get('class_type')}）に入力 '{b.input}' がありません", path))
    elif isinstance(inputs[b.input], list):
        issues.append(Issue("error", f"nodes.{label}: ノード '{b.node}' の入力 '{b.input}' は他ノードに接続されており、値を差し込めません", path))


def load_config(path: Path | str, url_override: str | None = None) -> tuple[ComfyConfig | None, list[Issue]]:
    """(設定, 問題の一覧)。error があれば設定は None（生成ボタンは無効になる）。"""
    path = Path(path)
    if not path.is_file():
        return None, [Issue("warning", f"設定ファイル {path} が見つかりません。ComfyUI連携は無効です", path)]
    issues: list[Issue] = []
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return None, [Issue("error", f"設定ファイルを読み込めません: {e}", path)]
    if not isinstance(data, dict):
        return None, [Issue("error", "設定ファイルのトップレベルがマッピングではありません", path)]

    comfy = data.get("comfyui") or {}
    nodes = data.get("nodes") or {}
    outputs_raw = data.get("outputs") or {}
    base = path.resolve().parent

    if not comfy.get("workflow"):
        return None, [Issue("error", "comfyui.workflow（ワークフローJSONのパス）が指定されていません", path)]
    wf_path = Path(comfy["workflow"])
    if not wf_path.is_absolute():
        wf_path = base / wf_path
    try:
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [Issue("error", f"ワークフロー {wf_path} が見つかりません", path)]
    except json.JSONDecodeError as e:
        return None, [Issue("error", f"ワークフロー {wf_path.name} がJSONとして不正です: {e}", path)]
    if not isinstance(wf, dict) or not all(isinstance(v, dict) and "class_type" in v for v in wf.values()):
        return None, [Issue("error", f"{wf_path.name} はAPI形式のワークフローではありません（ComfyUIの「API形式で保存」を使ってください）", path)]

    # 必須の割り当て
    bindings: dict[str, Binding | None] = {}
    for key in ("mossPositive", "animaPositive"):
        if key not in nodes:
            issues.append(Issue("error", f"nodes.{key} が指定されていません", path))
        bindings[key] = _binding(nodes.get(key), key, issues, path) if key in nodes else None
    for key in ("mossNegative", "animaNegative", "width", "height"):
        bindings[key] = _binding(nodes[key], key, issues, path) if nodes.get(key) else None
    if bool(bindings["width"]) != bool(bindings["height"]):
        issues.append(Issue("error", "nodes.width と nodes.height は両方指定するか、両方省略してください", path))

    seeds_raw = nodes.get("seed")
    seeds: list[Binding] = []
    if not seeds_raw:
        issues.append(Issue("error", "nodes.seed が指定されていません", path))
    else:
        for i, raw in enumerate(seeds_raw if isinstance(seeds_raw, list) else [seeds_raw]):
            b = _binding(raw, f"seed[{i}]", issues, path)
            if b:
                seeds.append(b)

    outputs: dict[str, list[str]] = {}
    for stage in STAGES:
        raw = outputs_raw.get(stage)
        ids = [str(x) for x in (raw if isinstance(raw, list) else [raw])] if raw else []
        if not ids:
            issues.append(Issue("error", f"outputs.{stage}（{STAGE_LABEL[stage]}の画像を出力するノード）が指定されていません", path))
        for nid in ids:
            node = wf.get(nid)
            if node is None:
                issues.append(Issue("error", f"outputs.{stage}: ノード '{nid}' がワークフローにありません", path))
            elif not any(k in node["class_type"] for k in ("SaveImage", "PreviewImage")):
                issues.append(Issue("error", f"outputs.{stage}: ノード '{nid}'（{node['class_type']}）は画像保存ノードではありません", path))
        outputs[stage] = ids

    # ワークフローとの突き合わせ
    for key, b in bindings.items():
        if b:
            _check_binding(wf, b, key, issues, path)
    for i, b in enumerate(seeds):
        _check_binding(wf, b, f"seed[{i}]", issues, path)

    if has_error(issues):
        return None, issues

    output_dir = comfy.get("outputDir")
    cfg = ComfyConfig(
        url=(url_override or comfy.get("url") or DEFAULT_URL).rstrip("/"),
        workflow_path=wf_path, workflow=wf,
        timeout_sec=float(comfy.get("timeoutSec", 600)),
        output_dir=Path(output_dir) if output_dir else None,
        moss_positive=bindings["mossPositive"], anima_positive=bindings["animaPositive"],
        moss_negative=bindings["mossNegative"], anima_negative=bindings["animaNegative"],
        seeds=seeds, width=bindings["width"], height=bindings["height"],
        outputs=outputs, base_dir=base,
    )
    return cfg, issues
