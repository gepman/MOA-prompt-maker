"""ワークフローへの値の差し込み（apply）と、割り当て候補の推定（suggest）。"""
from __future__ import annotations

import copy
from collections import deque

from moa.core.models import CompileResult

from .config import ComfyConfig, STAGES


def apply(workflow: dict, cfg: ComfyConfig, result: CompileResult, seed: int,
          denoise: float | None = None) -> dict:
    """プロンプト・シード・サイズを差し込んだワークフローを返す（元は変更しない）。

    ネガティブは「適用ON」のときだけ合成済みで置き換える。OFFのときはワークフローに
    書かれている既定をそのまま使う（ノードに触らない）。
    denoise は、設定で nodes.denoise が指定されていて値が渡されたときだけ書き換える。
    """
    wf = copy.deepcopy(workflow)

    def put(b, value):
        wf[b.node]["inputs"][b.input] = value

    put(cfg.moss_positive, result.moss_prompt)
    put(cfg.anima_positive, result.anima_prompt)
    if result.apply_negative:
        if cfg.moss_negative:
            put(cfg.moss_negative, result.negative_moss)
        if cfg.anima_negative:
            put(cfg.anima_negative, result.negative_anima)
    for b in cfg.seeds:
        put(b, seed)
    if cfg.denoise and denoise is not None:
        put(cfg.denoise, denoise)
    if cfg.width and cfg.height and result.canvas:
        put(cfg.width, result.canvas["w"])
        put(cfg.height, result.canvas["h"])
    return wf


# ---- 割り当て候補の推定 ----
def _is_link(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _upstream(wf: dict, nid: str) -> list[str]:
    return [v[0] for v in wf[nid].get("inputs", {}).values() if _is_link(v) and v[0] in wf]


def _bfs(wf: dict, start: str, pred, include_start: bool = False) -> str | None:
    """startの上流を近い順にたどり、最初に pred を満たしたノードidを返す。"""
    seen = {start}
    queue = deque([start] if include_start else _upstream(wf, start))
    seen.update(queue)
    while queue:
        nid = queue.popleft()
        if pred(nid):
            return nid
        for up in _upstream(wf, nid):
            if up not in seen:
                seen.add(up)
                queue.append(up)
    return None


def _is_text_node(wf: dict, nid: str) -> bool:
    return isinstance(wf[nid].get("inputs", {}).get("text"), str)


def _is_size_node(wf: dict, nid: str) -> bool:
    i = wf[nid].get("inputs", {})
    return isinstance(i.get("width"), int) and isinstance(i.get("height"), int)


def _title(wf: dict, nid: str) -> str:
    return (wf[nid].get("_meta") or {}).get("title", "")


def _hint(text: str) -> str | None:
    t = text.casefold()
    has_moss, has_anima = "moss" in t, "anima" in t
    return "moss" if has_moss and not has_anima else "anima" if has_anima and not has_moss else None


def _entry(wf: dict, nid: str, input_name: str) -> dict:
    return {"node": nid, "input": input_name, "title": _title(wf, nid), "class_type": wf[nid]["class_type"]}


def suggest(wf: dict) -> dict:
    """KSampler系を起点に、プロンプト・シード・サイズ・保存ノードの割り当て候補を推定する。

    段の判定は、サンプラー／プロンプトノードのタイトルに MOSS・Anima の語があればそれを優先し、
    なければ latent の由来（空latent＝t2i＝MOSS段 / VAEEncode＝i2i＝Anima段）で決める。
    """
    notes: list[str] = []
    nodes: dict[str, object] = {}
    samplers = [n for n, d in wf.items()
                if {"seed", "positive", "negative", "latent_image"} <= set(d.get("inputs", {}))]
    stage_of: dict[str, str] = {}
    size_node: dict[str, str] = {}

    for sid in sorted(samplers, key=lambda s: (len(s), s)):
        inputs = wf[sid]["inputs"]

        def text_node(link):
            if not _is_link(link) or link[0] not in wf:
                return None
            return link[0] if _is_text_node(wf, link[0]) else _bfs(wf, link[0], lambda n: _is_text_node(wf, n))

        pos, neg = text_node(inputs["positive"]), text_node(inputs["negative"])
        latent =inputs["latent_image"][0] if _is_link(inputs["latent_image"]) else None
        origin = _bfs(wf, latent, lambda n: _is_size_node(wf, n) or "VAEEncode" in wf[n]["class_type"],
                      include_start=True) if latent else None
        structural = None
        if origin:
            structural = "moss" if _is_size_node(wf, origin) else "anima"
        hinted = _hint(_title(wf, sid)) or (_hint(_title(wf, pos)) if pos else None)
        stage = hinted or structural
        if hinted and structural and hinted != structural:
            notes.append(f"サンプラー {sid} は、タイトルは{hinted}段ですが latent の由来は{structural}段です（タイトルを優先）")
        if stage is None:
            notes.append(f"サンプラー {sid} の段（MOSS/Anima）を判定できませんでした")
            continue
        if stage in stage_of.values():
            notes.append(f"{stage}段のサンプラーが複数あります（{sid} は割り当てていません）")
            continue
        stage_of[sid] = stage
        if stage == "anima" and isinstance(inputs.get("denoise"), (int, float)):
            nodes["denoise"] = _entry(wf, sid, "denoise")  # i2i 段のみ（t2i 段の denoise は 1 固定）
        if pos:
            nodes[f"{stage}Positive"] = _entry(wf, pos, "text")
        if neg:
            nodes[f"{stage}Negative"] = _entry(wf, neg, "text")
        if origin and _is_size_node(wf, origin):
            size_node[stage] = origin

    if "moss" in size_node:
        nodes["width"] = _entry(wf, size_node["moss"], "width")
        nodes["height"] = _entry(wf, size_node["moss"], "height")
    elif size_node:
        notes.append("画像サイズのノードがMOSS段にありません")
    nodes["seed"] = [_entry(wf, sid, "seed") for sid in stage_of]

    outputs: dict[str, list[str]] = {s: [] for s in STAGES}
    for nid, d in wf.items():
        if not any(k in d["class_type"] for k in ("SaveImage", "PreviewImage")):
            continue
        sampler = _bfs(wf, nid, lambda n: n in stage_of)
        if sampler is None:
            notes.append(f"保存ノード {nid} の上流にサンプラーが見つかりません")
        else:
            outputs[stage_of[sampler]].append(nid)
    for stage in STAGES:
        if f"{stage}Positive" not in nodes:
            notes.append(f"{stage}段のポジティブプロンプトのノードが見つかりません")
        if not outputs[stage]:
            notes.append(f"{stage}段の画像保存ノードが見つかりません")
    return {"nodes": nodes, "outputs": outputs, "notes": notes}


def suggestion_to_yaml(s: dict) -> str:
    """config.yaml の nodes/outputs に貼り付けられる形で出力する。"""
    def fmt(e: dict) -> str:
        return f'{{node: "{e["node"]}", input: {e["input"]}}}'

    lines = ["nodes:"]
    for key, e in s["nodes"].items():
        if key == "seed":
            lines.append("  seed:")
            lines += [f"    - {fmt(x)}   # {x['title']} ({x['class_type']})" for x in e]
        else:
            lines.append(f"  {key}: {fmt(e)}   # {e['title']} ({e['class_type']})")
    lines.append("outputs:")
    for stage, ids in s["outputs"].items():
        lines.append(f"  {stage}: [{', '.join(chr(34) + i + chr(34) for i in ids)}]")
    if s["notes"]:
        lines += ["", "# 注意"] + [f"# - {n}" for n in s["notes"]]
    return "\n".join(lines) + "\n"
